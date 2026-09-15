"""Freshness test for the generated `wy/wyrm/opcodes.wy` (epic 9, M2).

Mirrors `test_compiler_bc_format.py`'s check that the checked-in
`wyrm/opcode.h` matches `opcodes.c_header()`: `wy/wyrm/opcodes.wy` is
generated from the same `OPS` table by `tools/generate_opcode_wy.py`, so a
change to the table that skips regenerating it should fail here rather than
shipping a stale `opcodes.wy` (vm_plan/epic_9.md's M2 risk: "opcodes.py's OPS
table has ~430 rows; a hand-transcribed opcodes.wy would drift immediately").
"""

import importlib.util
import os
import sys

from conftest import REPO_ROOT

TOOLS_DIR = os.path.join(REPO_ROOT, "tools")


def _load_generator():
    """Import tools/generate_opcode_wy.py without requiring pypoc/tools to
    be a package (it's a standalone script, run via `.venv/bin/python
    tools/generate_opcode_wy.py`, same convention as generate_opcode_header.py)."""
    path = os.path.join(TOOLS_DIR, "generate_opcode_wy.py")
    spec = importlib.util.spec_from_file_location("generate_opcode_wy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_checked_in_opcodes_wy_matches_the_table():
    """A change to the opcode table that skips
    tools/generate_opcode_wy.py fails here rather than shipping a stale
    wy/wyrm/opcodes.wy."""
    generator = _load_generator()
    with open(generator.WY_TABLE_PATH) as f:
        assert f.read() == generator.opcode_wy(), (
            "wy/wyrm/opcodes.wy is out of date - run "
            "`pypoc/.venv/bin/python pypoc/tools/generate_opcode_wy.py`"
        )


def test_opcodes_wy_names_every_opcode_exactly_once():
    from wypoc.compiler_bc import opcodes

    generator = _load_generator()
    generated = generator.opcode_wy()
    for op in opcodes.OPS:
        assert f'"name": "{op.name}"' in generated
        if op.form == opcodes.PAIRABLE:
            assert f'"wide_name": "{op.wide_name}"' in generated
    assert generated.count('"name":') == len(opcodes.OPS)

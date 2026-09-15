#!/usr/bin/env python3
"""Generate the wyrm VM's opcode mnemonic table from the opcode set.

`wypoc/compiler_bc/opcodes.py` is the single source of truth for the v1
instruction set (doc/llm-bytecode.md section 3); this writes
`wy_opcode_names[256]`, a mnemonic lookup indexed by the raw opcode byte, for
the C VM's disassembler. cpoc's scripts/sync_pypoc_headers.py runs this after
copying opcode.h/image.h so the three files can never drift apart.

    .venv/bin/python tools/generate_opcode_names.py <output-path>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wypoc.compiler_bc import opcodes  # noqa: E402


def names_header() -> str:
    table = [None] * 256
    for op in opcodes.OPS:
        table[op.value] = op.name
        if op.form == opcodes.PAIRABLE:
            table[op.wide_value] = op.wide_name

    lines = [
        "/* wyrm opcode mnemonics - GENERATED from wypoc/compiler_bc/opcodes.py",
        " * by pypoc/tools/generate_opcode_names.py. Do not hand-edit.",
        " *",
        " * Indexed by the raw opcode byte (WYRM_OP(code)); a byte no opcode",
        " * uses is NULL (static storage zero-initializes the rest of the",
        " * array). */",
        "#ifndef WYRM_OPCODE_NAMES_H",
        "#define WYRM_OPCODE_NAMES_H",
        "",
        "static const char* const wy_opcode_names[256] = {",
    ]
    for value, name in enumerate(table):
        if name is not None:
            lines.append(f'    [0x{value:02X}] = "{name}",')
    lines.append("};")
    lines.append("")
    lines.append("#endif /* WYRM_OPCODE_NAMES_H */")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: generate_opcode_names.py <output-path>", file=sys.stderr)
        return 2
    out_path = sys.argv[1]
    generated = names_header()
    existing = None
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = f.read()
    if existing == generated:
        print(f"{out_path}: already up to date")
        return 0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(generated)
    print(f"{out_path}: regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

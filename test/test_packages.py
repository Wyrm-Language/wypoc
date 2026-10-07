"""Packages and namespace packages (project design/modules.md M1).

Resolution follows Python's FileFinder: a package (`name/__init__.wy`) beats
a module (`name.wy`), which beats a bare directory, and a bare directory is a
namespace package that may span several roots. A package's children come
from its own directory. A package still running its __init__.wy is passed
through, not reported as a cycle, when an import is only on its way to one
of the package's children."""
import os

import pytest

from wypoc import wyrm_modules
from wypoc.parse import parse
from wypoc.wyrm_eval_parse_tree import (
    ImportCycleError,
    Module,
    clear_module_cache,
    eval_program,
)


def _tree(root, files: dict):
    """Write `files` (relative path -> source) under `root`."""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Two search roots, r1 before r2, as WYRM_PATH."""
    r1, r2 = tmp_path / "r1", tmp_path / "r2"
    r1.mkdir()
    r2.mkdir()
    monkeypatch.setenv("WYRM_PATH", f"{r1}:{r2}")
    clear_module_cache()
    yield r1, r2
    clear_module_cache()


def _run(source: str) -> dict:
    ctx: dict = {}
    eval_program(parse(source), ctx)
    return ctx


def _value(ctx, name):
    return ctx[name].value


# --- resolution -------------------------------------------------------------

def test_a_package_beats_a_module_in_the_same_root(roots):
    r1, _ = roots
    _tree(r1, {"pkg/__init__.wy": "", "pkg.wy": ""})
    resolved = wyrm_modules.resolve_module(["pkg"])
    assert resolved.kind == "package"
    assert resolved.file == str(r1 / "pkg" / "__init__.wy")


def test_a_module_beats_a_bare_directory_and_has_no_children(roots):
    r1, _ = roots
    _tree(r1, {"m.wy": "", "m/x.wy": ""})
    assert wyrm_modules.resolve_module(["m"]).kind == "module"
    assert wyrm_modules.resolve_module(["m", "x"]) is None


def test_a_bare_directory_is_a_namespace_package(roots):
    r1, _ = roots
    _tree(r1, {"ns/leaf.wy": ""})
    resolved = wyrm_modules.resolve_module(["ns"])
    assert resolved.kind == "namespace" and resolved.file is None
    assert wyrm_modules.resolve_module_file(["ns"]) is None, "no file to run"
    assert wyrm_modules.resolve_module(["ns", "leaf"]).kind == "module"


def test_a_namespace_package_spans_roots(roots):
    r1, r2 = roots
    _tree(r1, {"ns/a.wy": ""})
    _tree(r2, {"ns/b.wy": ""})
    assert wyrm_modules.resolve_module(["ns"]).dirs == [str(r1 / "ns"), str(r2 / "ns")]
    assert wyrm_modules.resolve_module(["ns", "a"]).file == str(r1 / "ns" / "a.wy")
    assert wyrm_modules.resolve_module(["ns", "b"]).file == str(r2 / "ns" / "b.wy")


def test_a_regular_package_takes_children_only_from_its_own_directory(roots):
    r1, r2 = roots
    _tree(r1, {"pkg/__init__.wy": ""})
    _tree(r2, {"pkg/extra.wy": ""})
    assert wyrm_modules.resolve_module(["pkg", "extra"]) is None


def test_a_package_in_an_earlier_root_beats_a_namespace_in_a_later_one(roots):
    r1, r2 = roots
    _tree(r1, {"ns/a.wy": ""})
    _tree(r2, {"ns/__init__.wy": "", "ns/b.wy": ""})
    resolved = wyrm_modules.resolve_module(["ns"])
    assert resolved.kind == "package" and resolved.dirs == [str(r2 / "ns")]
    assert wyrm_modules.resolve_module(["ns", "a"]) is None


# --- namespace packages at run time ---------------------------------------

def test_importing_through_a_namespace_package(roots):
    r1, r2 = roots
    _tree(r1, {"ns/a.wy": "x := 1\n"})
    _tree(r2, {"ns/b.wy": "y := 2\n"})
    ctx = _run("import ns::a\nimport ns::b\nv := ns::a::x + ns::b::y\n")
    assert _value(ctx, "v") == 3
    ns = _value(ctx, "ns")
    assert isinstance(ns, Module) and ns.is_package and ns.path is None


def test_a_namespace_package_imports_on_its_own(roots):
    r1, _ = roots
    _tree(r1, {"ns/a.wy": ""})
    ctx = _run("import ns\n")
    assert isinstance(_value(ctx, "ns"), Module)


def test_a_namespace_package_has_no_members_of_its_own(roots):
    r1, _ = roots
    _tree(r1, {"ns/a.wy": ""})
    with pytest.raises(NameError):
        _run("import ns\nv := ns::len\n")


# --- ancestor pass-through --------------------------------------------------

def test_a_package_reexports_its_own_child(roots):
    """The case wyrm-v0's compiler.wy marker existed for."""
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "import pkg::impl::(compile)\n",
        "pkg/impl.wy": "fn compile(x):\n    return x * 2\n",
    })
    ctx = _run("import pkg\nv := pkg::compile(21)\n")
    assert _value(ctx, "v") == 42


def test_a_wildcard_reexport_is_part_of_the_package(roots):
    """Python style: whatever a package's top level binds, imported names
    included, is what `import pkg::*` offers."""
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "import pkg::impl::*\n",
        "pkg/impl.wy": "answer := 42\n",
    })
    ctx = _run("import pkg::*\nv := answer\n")
    assert _value(ctx, "v") == 42


def test_a_child_imports_a_sibling_while_its_package_initialises(roots):
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "import pkg::front::(run)\n",
        "pkg/front.wy": "import pkg::helpers\nfn run():\n    return helpers::base + 1\n",
        "pkg/helpers.wy": "base := 9\n",
    })
    ctx = _run("import pkg\nv := pkg::run()\n")
    assert _value(ctx, "v") == 10


def test_a_child_importing_its_own_package_is_a_cycle(roots):
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "import pkg::child\n",
        "pkg/child.wy": "import pkg\n",
    })
    with pytest.raises(ImportCycleError, match="pkg::child imports pkg"):
        _run("import pkg\n")


def test_a_cycle_is_not_retried_as_a_module_member(roots):
    """`import a::b` falls back to reading `b` out of `a` when `a::b` isn't a
    module. A cycle mustn't take that path and lose its message."""
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "import pkg::child\n",
        "pkg/child.wy": "import pkg::child\n",
    })
    with pytest.raises(ImportCycleError):
        _run("import pkg\n")


def test_reading_a_passed_through_package_member_is_an_error(roots):
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "early := 1\nimport pkg::child\n",
        "pkg/child.wy": "import pkg::sibling\nv := pkg::early\n",
        "pkg/sibling.wy": "",
    })
    with pytest.raises(ImportError, match="'pkg' is still initialising"):
        _run("import pkg\n")


def test_a_passed_through_package_is_readable_once_initialised(roots):
    """The restriction is on the window, not the binding: a function that
    reads the parent later, after its init has finished, sees it."""
    r1, _ = roots
    _tree(r1, {
        "pkg/__init__.wy": "import pkg::child::(later)\nlate := 5\n",
        "pkg/child.wy": "import pkg::sibling\nfn later():\n    return pkg::late\n",
        "pkg/sibling.wy": "",
    })
    ctx = _run("import pkg\nv := pkg::later()\n")
    assert _value(ctx, "v") == 5


def test_packages_on_the_path_run_once_outermost_first(roots):
    r1, _ = roots
    _tree(r1, {
        "a/__init__.wy": "import order\norder::seen ! append(\"a\")\n",
        "a/b/__init__.wy": "import order\norder::seen ! append(\"b\")\n",
        "a/b/c.wy": "import order\norder::seen ! append(\"c\")\n",
        "order.wy": "seen := []\n",
    })
    ctx = _run("import a::b::c\nimport a::b\nimport order\nv := order::seen\n")
    assert list(_value(ctx, "v")) == ["a", "b", "c"]

"""Every name placed before a module runs (project design/modules.md M2).

wypoc/names.py approximates a compiler's name placement: an undefined name,
or a name two wildcard imports supply from different bindings, is reported
before the module's first statement runs."""
import pytest

from wypoc import names
from wypoc.parse import parse
from wypoc.wyrm_eval_parse_tree import clear_module_cache, eval_program


def _tree(root, files: dict):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("WYRM_PATH", str(tmp_path))
    clear_module_cache()
    _tree(tmp_path, {
        "palette.wy": "mix := 1\nred := 2\n",
        "pigment.wy": "mix := 3\n",
        "shades.wy": "import palette::*\n",
    })
    yield tmp_path
    clear_module_cache()


def _check(source: str, root=None) -> list:
    roots = [str(root)] if root is not None else None
    return [str(e) for e in names.check_names(parse(source), roots)]


def test_defined_names_pass(root):
    assert _check("x := 1\nfn f(a):\n    return a + x\nprintln(f(2))\n", root) == []


def test_an_undefined_name_is_reported_with_its_place(root):
    assert _check("x := 1\ny := x + z\n", root) == ["2:10: undefined name 'z'"]


def test_a_wildcard_import_defines_what_it_offers(root):
    assert _check("import palette::*\nprintln(red)\n", root) == []
    assert _check("import palette::* except (red)\nprintln(red)\n", root) == [
        "2:9: undefined name 'red'"]


def test_an_unreadable_wildcard_turns_the_undefined_check_off(root):
    assert _check("import nowhere::*\nprintln(anything)\n", root) == []


def test_a_used_wildcard_collision_is_reported(root):
    errors = _check("import palette::*\nimport pigment::*\nprintln(mix)\n", root)
    assert len(errors) == 1 and "ambiguous name 'mix'" in errors[0]


def test_an_unused_collision_is_not(root):
    assert _check("import palette::*\nimport pigment::*\nprintln(red)\n", root) == []


def test_one_binding_reached_twice_is_not_a_collision(root):
    assert _check("import palette::*\nimport shades::*\nprintln(mix)\n", root) == []


def test_a_local_definition_settles_a_collision(root):
    assert _check("import palette::*\nimport pigment::*\nmix := 9\nprintln(mix)\n", root) == []


def test_a_decorated_expression_is_the_decorators_language():
    # `@accept apply_if(...)` hands its tree to the decorator; the names in
    # it aren't this module's.
    source = "fn f():\n    @accept apply_if(_as_list, value(1))\n"
    assert _check(source) == []


def test_an_import_is_refused_before_the_module_runs(root):
    _tree(root, {"typo.wy": "println(\"typo ran\")\nx := undefined_thing\n"})
    with pytest.raises(names.NameCheckError, match="undefined name 'undefined_thing'"):
        eval_program(parse("import typo\n"), {})


def test_module_exports_lists_what_a_wildcard_offers(root):
    """wyrm-v0's compiler reads its dependencies' exports through this."""
    from wypoc.wyrm_builtins import is_error, module_exports
    assert module_exports("palette") == ["mix", "red"]
    assert module_exports("shades") == ["mix", "palette", "red"], "re-exports count, and so does the binding"
    assert is_error(module_exports("nowhere"))


def test_module_exports_answers_what_an_image_would_export(tmp_path, monkeypatch):
    """wyrm-v0's compiler numbers slots by these names: a module exports what it binds
    by importing, its `__` names and its functions' statics (names.image_exports)."""
    from wypoc.wyrm_builtins import module_exports
    (tmp_path / "leaf.wy").write_text("x := 1\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sub.wy").write_text("y := 2\nz := 3\n")
    (tmp_path / "top.wy").write_text(
        "import pkg::sub::(y)\nimport leaf::*\n"
        "fn __main():\n    pass\n\nfn count():\n    static n: int = 0\n    return n\n")
    monkeypatch.setenv("WYRM_PATH", str(tmp_path))
    assert module_exports("top") == sorted(
        ["__main", "count", "count::n", "leaf", "pkg", "sub", "x", "y"])

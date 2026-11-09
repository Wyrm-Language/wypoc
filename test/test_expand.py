"""What wyrm-v0's self-hosted compiler needs to run from source (wypoc/wyrm_expand.py)."""
from wypoc import sexpr, wyrm_eval_parse_tree as ev
from wypoc.parse import parse
from wypoc.wyrm_builtins import Symbol, WyrmError, cons, error_message, is_error, substr
from wypoc.wyrm_expand import bind_message, expand, parse_tree


def test_error_message():
    assert error_message(WyrmError("boom")) == "boom"
    assert error_message(1) is ev.wyrm_builtins.NIL


def test_substr_is_also_a_plain_function():
    ctx = ev.Scope()
    ev.populate_globals(ctx)
    ev.eval_program(parse('a := substr("asdf", 1, 2)\nb := "asdf" ! substr(1, 2)\n'), ctx)
    assert ev.lookup("a", ctx) == "sd" == ev.lookup("b", ctx)


def test_a_box_of_an_s_expression_reads_back_as_itself():
    tree = cons(Symbol("int"), cons(1, ev.wyrm_builtins.NIL))
    assert ev.sexpr_value({}, ev.tree_box(tree)) is tree


def test_parse_tree_is_the_canonical_tree():
    tree = parse_tree("x := (a.b) ! m(1)\n")
    assert sexpr.encode(parse("x := (a.b) ! m(1)\n")) == tree


def _module(tmp_path, monkeypatch, name, source):
    (tmp_path / (name + ".wy")).write_text(source)
    monkeypatch.setenv("WYRM_PATH", str(tmp_path))
    ev.clear_module_cache()
    return ev.import_module([name])


def test_bind_message_answers_a_callable_or_an_error(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch, "scoped", "fn [TreeBase] twice():\n    return 2\n")
    box = ev.tree_box(1)
    assert ev.call_value(bind_message(mod, box, Symbol("twice")), [], {}) == 2
    assert is_error(bind_message(mod, box, "absent"))
    assert is_error(bind_message(mod, 1, "twice"))


def test_expand_applies_the_decorators_a_module_imports(tmp_path, monkeypatch):
    (tmp_path / "deco.wy").write_text(
        "fn [TreeBase] as_five():\n    return $['int, 5]\n")
    (tmp_path / "expander.wy").write_text(
        "fn expand_tree(tree, scope):\n"
        "    return bind_message(scope, tree_box(tree), 'as_five)()\n")
    monkeypatch.setenv("WYRM_PATH", str(tmp_path))
    ev.clear_module_cache()
    tree = sexpr.encode(parse("import deco::*\nx := 1\n"))
    assert sexpr.encode(parse("5\n")).cdr.car == expand(tree, None, "expander")

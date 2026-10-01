"""The evaluator side of the canonical-AST syntax decisions (the wyrm
project's design/syntax.md): virtual slots (G3), `this`/`super` as names
the method binds (G4), `name::$ast` as an ordinary path (G7), `try`/`catch`
precedence (G1), plus the constructs the canonical tree added - anonymous
classes and coroutines, `break x`, `not in`, single inheritance."""
import pytest

from wypoc.parse import parse
from wypoc.wyrm_builtins import Symbol
from wypoc.wyrm_eval_parse_tree import Scope, eval_program, populate_globals


def run(src: str) -> dict:
    ctx = Scope()
    populate_globals(ctx)
    eval_program(parse(src), ctx)
    return ctx


def value(src: str, name: str = "result"):
    return run(src)[name].value


# --- G3: virtual slots -----------------------------------------------------

PERSON = """
class person:
    slot birth: int = 1990
    slot age: int:
        now := 2026
        fn getter(): now - this.birth
        fn setter(age: int): this.birth = now - age
    slot id: int:
        fn getter(): 7
    slot secret:
        fn setter(v): this.birth = v
p := person()
"""


def test_a_virtual_slot_reads_through_its_getter():
    assert value(PERSON + "result := p.age\n") == 36


def test_a_virtual_slot_writes_through_its_setter():
    assert value(PERSON + "p.age = 6\nresult := p.birth\n") == 2020


def test_a_virtual_slot_without_a_setter_is_read_only():
    with pytest.raises(TypeError, match="read-only"):
        run(PERSON + "p.id = 1\n")


def test_a_virtual_slot_without_a_getter_is_write_only():
    assert "write-only" in str(value(PERSON + "result := p.secret\n"))


def test_a_virtual_slot_has_no_storage():
    ctx = run(PERSON)
    assert "age" not in ctx["p"].value.attrs


def test_a_virtual_slot_is_inherited():
    src = PERSON + "class student(person):\n    pass\nresult := student().age\n"
    assert value(src) == 36


def test_a_virtual_slot_cannot_have_a_default():
    with pytest.raises(TypeError, match="can't have a default"):
        run("class A:\n    slot age: int = 0:\n        fn getter(): 1\n")


def test_a_virtual_slot_block_defines_only_getter_and_setter():
    with pytest.raises(TypeError, match="only `fn getter` and `fn setter`"):
        run("class A:\n    slot age:\n        fn helper(): 1\n")
    with pytest.raises(TypeError, match="not a ExprStmt"):
        run("class A:\n    slot age:\n        f()\n")


def test_with_is_an_ordinary_name():
    assert value("with := 3\nresult := with + 1\n") == 4


# --- G4: `this` and `super` ------------------------------------------------

def test_this_is_the_receiver():
    src = "class A:\n    slot n: int = 4\nfn [A] me(): this\na := A()\nresult := a ! me()\n"
    ctx = run(src)
    assert ctx["result"].value is ctx["a"].value


def test_super_calls_the_next_more_general_method():
    src = (
        "class A:\n    slot n: int = 1\n"
        "class B(A):\n    pass\n"
        "fn [A] get(): this.n\n"
        "fn [B] get(): super() + 10\n"
        "result := B() ! get()\n"
    )
    assert value(src) == 11


def test_super_passes_its_arguments():
    src = (
        "class A:\n    pass\nclass B(A):\n    pass\n"
        "fn [A] add(x): x + 1\n"
        "fn [B] add(x): super(x * 10)\n"
        "result := B() ! add(2)\n"
    )
    assert value(src) == 21


def test_super_is_a_value():
    src = (
        "class A:\n    pass\nclass B(A):\n    pass\n"
        "fn [A] get(): 5\n"
        "fn [B] get():\n    f := super\n    return f()\n"
        "result := B() ! get()\n"
    )
    assert value(src) == 5


def test_super_with_no_more_general_method_is_an_error():
    src = "class A:\n    pass\nfn [A] get(): super()\nresult := A() ! get()\n"
    with pytest.raises(TypeError, match="no more general method"):
        run(src)


def test_this_and_super_are_unbound_outside_a_method():
    with pytest.raises(NameError):
        run("x := this\n")
    with pytest.raises(NameError):
        run("x := super\n")


def test_defined_is_an_ordinary_call():
    assert value("fn defined(x): x == 'foo\nresult := defined('foo)\n") is True
    with pytest.raises(NameError):
        run("x := defined('foo)\n")


# --- G7: `name::$ast` -----------------------------------------------------

def test_dollar_ast_is_the_definitions_tree():
    src = "fn twice(x): x * 2\nresult := car(sexpr(twice::$ast))\n"
    assert value(src) == Symbol("fn_def")


def test_other_dollar_names_are_ordinary():
    assert value("$other := 1\nresult := $other + 1\n") == 2


# --- G1: `try` and `catch` -------------------------------------------------

FAILS = "fn bad(): error(\"bad\")\n"


def test_catch_inside_try_supplies_the_fallback():
    assert value(FAILS + "fn f():\n    return try bad() catch 3\nresult := f()\n") == 3


def test_try_returns_only_if_the_handler_fails_too():
    src = FAILS + "fn f():\n    v := try bad() catch bad()\n    return 1\nresult := f()\n"
    assert "bad" in str(value(src))


def test_catch_nests_right():
    assert value(FAILS + "result := bad() catch bad() catch 5\n") == 5


# --- the rest ---------------------------------------------------------------

def test_an_anonymous_class():
    src = "K := class:\n    slot n: int = 3\nresult := K().n\n"
    assert value(src) == 3


def test_an_anonymous_class_with_a_base():
    src = (
        "class A:\n    slot n: int = 1\nfn [A] get(): this.n * 2\n"
        "K := class(A):\n    slot n: int = 20\n"
        "result := K() ! get()\n"
    )
    assert value(src) == 40


def test_an_anonymous_coroutine():
    src = (
        "c := co(<- int, start: int):\n"
        "    n := start\n"
        "    while true:\n"
        "        n = n + (yield n)\n"
        "g := c(1)\n"
        "first := next(g)\n"
        "result := send(g, 5)\n"
    )
    ctx = run(src)
    assert ctx["first"].value == 1
    assert ctx["result"].value == 6


def test_a_lambda_argument_ends_at_the_comma():
    assert value("fn ap(f, x): f(x)\nresult := ap(fn(x): x * 2, 21)\n") == 42


def test_break_gives_the_loop_its_value():
    assert value("result := do:\n    while true: break 42\n") == 42
    assert value("result := do:\n    for x in [1, 2, 3]:\n        if x == 2: break x * 10\n") == 20


def test_not_in():
    assert value("result := 3 not in [1, 2]\n") is True
    assert value("result := 1 not in [1, 2]\n") is False


def test_defer_on_any_type_runs_only_on_an_error_exit():
    src = (
        "log := []\n"
        "fn f(fail):\n"
        "    defer on error | nil: log ! append(1)\n"
        "    if fail: return error(\"x\")\n"
        "    return 0\n"
        "f(false)\nf(true)\n"
        "result := len(log)\n"
    )
    assert value(src) == 1


def test_a_class_has_at_most_one_base():
    with pytest.raises(SyntaxError):
        parse("class A(B, C):\n    pass\n")


def test_a_union_annotation_parses_and_is_kept():
    from wypoc import ast_nodes as ast

    decl = parse("var x: int | str = 1\n").body[0]
    assert isinstance(decl.targets[0].type, ast.TypeUnion)
    assert value("var x: int | str = 1\nresult := x\n") == 1


def test_a_multiline_triple_quoted_string():
    assert value('result := """tri\nple"""\n') == "tri\nple"


@pytest.mark.parametrize("src,message", [
    ("this := 1\n", "1:1: `this` can't be assigned"),
    ("this = 1\n", "1:1: `this` can't be assigned"),
    ("super := 1\n", "1:1: `super` can't be assigned"),
    ("fn f(this): 1\n", "1:6: `this` can't be declared"),
    ("f := fn(this): 1\n", "1:9: `this` can't be declared"),
    ("fn g():\n    for super in xs: pass\n", "2:9: `super` can't be declared"),
])
def test_this_and_super_cannot_be_declared_or_assigned(src, message):
    """Checked before anything runs, as a compiler would (G0, G4)."""
    from wypoc.wyrm_eval_parse_tree import CompileError

    with pytest.raises(CompileError) as excinfo:
        run(src)
    assert str(excinfo.value) == message


def test_this_attributes_can_be_assigned():
    src = "class A:\n    slot n: int = 0\nfn [A] set(v): this.n = v\na := A()\na ! set(3)\nresult := a.n\n"
    assert value(src) == 3

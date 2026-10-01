"""Exercises the canonical tree bridge (wypoc/sexpr.py) directly: the shape
each kind encodes to, that every kind round-trips, and that a construct or a
tree the format doesn't carry fails by name rather than producing a
half-translated tree. Shapes are written in the D2 printed form (see
wypoc/sexp_print.py), as the conformance corpus writes them."""
from pathlib import Path

import pytest

from conftest import SAMPLES_DIR
from wypoc import ast_nodes as ast
from wypoc import sexpr
from wypoc.parse import parse
from wypoc.sexp_print import write
from wypoc.wyrm_builtins import NIL, Pair, Symbol


def encode_first(src: str):
    """The tree of `src`'s first statement."""
    return sexpr.encode(parse(src).body[0])


def encoded(src: str) -> str:
    return write(encode_first(src))


def round_trip(tree):
    """encode -> decode -> encode, printed."""
    return write(sexpr.encode(sexpr.decode(tree)))


# One row per kind, so the table is also the reference for what a decorator
# sees. Expression kinds ride in on a `:=`, whose `define` wrapper is part
# of the expected text.
SHAPES = [
    ("x := 41", "(define x (type auto) (int 41))"),
    ("x := 2.5", "(define x (type auto) (float 2.5))"),
    ('x := "hi"', '(define x (type auto) (str "hi"))'),
    ("x := true", "(define x (type auto) (true))"),
    ("x := false", "(define x (type auto) (false))"),
    ("x := nil", "(define x (type auto) (nil))"),
    ("x := ...", "(define x (type auto) (ellipsis))"),
    ("x := 'name", "(define x (type auto) (sym name))"),
    ("x := \\a", "(define x (type auto) (char 97))"),
    ("x := \\newline", "(define x (type auto) (char 10))"),
    ("x := y", "(define x (type auto) y)"),
    ("x := this", "(define x (type auto) this)"),
    ("x := [1, 2]", "(define x (type auto) (array (int 1) (int 2)))"),
    ("x := (1, 2)", "(define x (type auto) (tuple (int 1) (int 2)))"),
    ("x := $[1, 2]", "(define x (type auto) (list (int 1) (int 2)))"),
    ('x := {"a": 1}', '(define x (type auto) (dict ((str "a") (int 1))))'),
    ("x := a + b", "(define x (type auto) (+ a b))"),
    ("x := a <=> b", "(define x (type auto) (<=> a b))"),
    ("x := a << b", "(define x (type auto) (<< a b))"),
    ("x := a in b", "(define x (type auto) (in a b))"),
    ("x := a not in b", "(define x (type auto) (not (in a b)))"),
    ("x := -a", "(define x (type auto) (neg a))"),
    ("x := +a", "(define x (type auto) (pos a))"),
    ("x := ~a", "(define x (type auto) (~ a))"),
    ("x := a and b and c", "(define x (type auto) (and a b c))"),
    ("x := a or b", "(define x (type auto) (or a b))"),
    ("x := not a", "(define x (type auto) (not a))"),
    ("x := f(1)", "(define x (type auto) (apply f (int 1)))"),
    ("x := f(a, *b, k=1, **c)",
     "(define x (type auto) (apply f a (spread b) (kwarg k (int 1)) (spread_kw c)))"),
    ("x := a.b", "(define x (type auto) (attr a b))"),
    ("x := a[0]", "(define x (type auto) (index a (int 0)))"),
    ("x := o ! m(1)", "(define x (type auto) (apply (bind_msg o m) (int 1)))"),
    ("x := o ! m", "(define x (type auto) (bind_msg o m))"),
    ("x := o ! mod::m()", "(define x (type auto) (apply (bind_msg o (:: mod m))))"),
    ("x := (a, b) ! m()", "(define x (type auto) (apply (bind_msg (tuple a b) m)))"),
    ("x := m::C", "(define x (type auto) (:: m C))"),
    ("x := f::$ast", "(define x (type auto) (:: f $ast))"),
    ("x := super(1)", "(define x (type auto) (apply super (int 1)))"),
    ("x := v is int", "(define x (type auto) (is v (type int)))"),
    ("x := v is int | nil", "(define x (type auto) (is v (type int nil)))"),
    ("x := v is not int", "(define x (type auto) (not (is v (type int))))"),
    ("x := try v", "(define x (type auto) (try v))"),
    ("x := try v catch 0", "(define x (type auto) (try (catch v (int 0))))"),
    ("x := v catch 0", "(define x (type auto) (catch v (int 0)))"),
    ("x := v catch return 0", "(define x (type auto) (catch v (return (int 0))))"),
    ("x := fn(a): a", "(define x (type auto) (lambda ((a (type auto) ())) (type auto) a))"),
    ("x := co(<- int, a): yield a",
     "(define x (type auto) (co_lambda ((a (type auto) ())) (type int) (type auto) (yield a)))"),
    ("x := class(B) { slot a; }",
     "(define x (type auto) (class_expr B (slot_def a (type auto) () ())))"),
    ("x := @d(1) 3", "(define x (type auto) (decorate d (int 3) (int 1)))"),
    ("x := y ?= 1", "(define x (type auto) (if_set y (int 1)))"),
    ("f(x)", "(apply f x)"),
    ("x = 1", "(set x (int 1))"),
    ("x = 1, 2", "(set x (tuple (int 1) (int 2)))"),
    ("x ?= 1", "(if_set x (int 1))"),
    ("a.b = 1", "(set (attr a b) (int 1))"),
    ("a[0] = 1", "(set (index a (int 0)) (int 1))"),
    ("this.a = 1", "(set (attr this a) (int 1))"),
    ("a, b = b, a", "(set_values (a b) (tuple b a))"),
    ("a, b = f()", "(set_values (a b) (apply f))"),
    ("var a: int, b = 1, 2", "(define_values ((a (type int)) (b (type auto))) (tuple (int 1) (int 2)))"),
    ("a, b := f()", "(define_values ((a (type auto)) (b (type auto))) (apply f))"),
    ("var z", "(define z (type auto) ())"),
    ("var z: a::T | nil", "(define z (type (:: a T) nil) ())"),
    ("static s: int = 0", "(static s (type int) (int 0))"),
    ("break", "(break)"),
    ("break 1", "(break (int 1))"),
    ("continue", "(continue)"),
    ("pass", "(pass)"),
    ("return", "(return)"),
    ("return x", "(return x)"),
    ("yield", "(yield)"),
    ("yield x", "(yield x)"),
    ("yield from g()", "(yield_from (apply g))"),
    ("slot count: int = 0", "(slot_def count (type int) (int 0) ())"),
    ("import a", "(import a)"),
    ("import a::b", "(import (:: a b))"),
    ("import a::b as c", "(import (:: a b) (as c))"),
    ("import a::(x, y as z)", "(import a (items (item x ()) (item y z)))"),
    ("import a::*", "(import a (all))"),
    ("import a::* except (b, c)", "(import a (all b c))"),
    ("import static a::b", "(import_static (:: a b))"),
]


@pytest.mark.parametrize("src,expected", SHAPES, ids=[s for s, _ in SHAPES])
def test_kind_encodes_to_its_documented_shape(src, expected):
    assert encoded(src) == expected


@pytest.mark.parametrize("src", [s for s, _ in SHAPES], ids=[s for s, _ in SHAPES])
def test_every_kind_round_trips(src):
    once = encode_first(src)
    assert round_trip(once) == write(once)


MULTILINE = [
    ("do", "x := do:\n    1\n", "(define x (type auto) (do (int 1)))"),
    ("while", "while c:\n    f()\n    g()\n", "(while c (do (apply f) (apply g)))"),
    ("while one", "while c { f() }\n", "(while c (apply f))"),
    ("for", "for i in xs:\n    pass\n", "(for i xs () (pass))"),
    ("for else", "for i in xs: f(i)\nelse: g()\n", "(for i xs (apply g) (apply f i))"),
    ("if", "if c:\n    pass\n", "(cond (c (pass)))"),
    ("if chain", "if a: 1\nelif b: 2\nelse: 3\n",
     "(cond (a (int 1)) (b (int 2)) (else (int 3)))"),
    ("defer", "defer:\n    pass\n", "(defer (pass))"),
    ("defer_on", "defer on error:\n    pass\n", "(defer_on (type error) (pass))"),
    ("fn", "fn [A, B] m(a: int, b = 2, *r, **k) -> str:\n    log(a)\n    a\n",
     "(fn_def m (dispatch (type A) (type B)) ((a (type int) ()) (b (type auto) (int 2)) "
     "(* r (type auto)) (** k (type auto))) (type str) (do (apply log a) a))"),
    ("fn empty dispatch", "fn [] m(): 1\n", "(fn_def m (dispatch) () (type auto) (int 1))"),
    ("co", "co g(<- int, n) -> int: yield n\n",
     "(co_def g () ((n (type auto) ())) (type int) (type int) (yield n))"),
    ("class", "class A(B):\n    slot n: int = 0\n    fn [A] get(): this.n\n",
     "(class_def A B (do (slot_def n (type int) (int 0) ()) "
     "(fn_def get (dispatch (type A)) () (type auto) (attr this n))))"),
    ("class empty", "class A {}\n", "(class_def A () (pass))"),
    ("virtual slot", "class A:\n    slot age: int:\n        fn getter(): 1\n",
     "(class_def A () (slot_def age (type int) () (fn_def getter () () (type auto) (int 1))))"),
    ("decorated", "@a(1)\n@b\nfn g(): 1\n",
     "(decorate a (decorate b (fn_def g () () (type auto) (int 1))) (int 1))"),
]


@pytest.mark.parametrize("name,src,expected", MULTILINE, ids=[m[0] for m in MULTILINE])
def test_multiline_kinds(name, src, expected):
    assert encoded(src) == expected
    once = encode_first(src)
    assert round_trip(once) == write(once)


def test_module_splices_its_statements():
    tree = parse("x := 1\ny := 2\n")
    assert write(sexpr.encode(tree)) == (
        "(module (define x (type auto) (int 1)) (define y (type auto) (int 2)))")
    back = sexpr.decode(sexpr.encode(tree))
    assert [type(s).__name__ for s in back.body] == ["VarDecl", "VarDecl"]


def test_an_expression_in_a_body_comes_back_as_a_statement():
    """There is no statement wrapper on the wire (R2), so decoding a body
    puts ExprStmt back around what isn't a statement."""
    fn = sexpr.decode(encode_first("fn f():\n    g()\n    1\n"))
    assert [type(s).__name__ for s in fn.body] == ["ExprStmt", "ExprStmt"]


def test_if_set_is_a_statement_or_an_expression_by_position():
    stmt = sexpr.decode(encode_first("x ?= 1"))
    assert isinstance(stmt, ast.Assign) and stmt.op == "?="
    decl = sexpr.decode(encode_first("y := x ?= 1"))
    assert isinstance(decl.values[0], ast.SetIfUnset)


def test_annotate_crosses_both_ways():
    tree = sexpr.node("annotate", Symbol("template"), sexpr.node("true"),
                      encode_first("fn f(): 1"))
    back = sexpr.decode(tree)
    assert isinstance(back, ast.Annotate) and back.key == "template"
    assert isinstance(back.target, ast.FnDef)
    assert write(sexpr.encode(back)) == (
        "(annotate template (true) (fn_def f () () (type auto) (int 1)))")


def test_a_form_list_may_come_back_as_a_python_list():
    """A decorator may build a headless list (here a parameter list) out of
    an array; a pair list is what the encoder makes, but both are
    accepted."""
    tree = sexpr.node("lambda", [], sexpr.node("type", Symbol("auto")), sexpr.node("int", 1))
    assert sexpr.decode(tree).params == []


def _samples():
    skip = ("signal", "emit", "thread", "task ")
    for path in sorted(Path(SAMPLES_DIR).glob("*.wy")):
        text = path.read_text()
        if not any(word in text for word in skip):
            yield path


@pytest.mark.parametrize("path", list(_samples()), ids=lambda p: p.name)
def test_every_sample_round_trips(path):
    """Every sample (those using wypoc's own signal/emit/task/thread aside,
    which have no canonical tree yet) survives encode -> decode -> encode."""
    once = sexpr.encode(parse(path.read_text()))
    assert round_trip(once) == write(once)


# --- failing loudly -------------------------------------------------------

CANNOT_CROSS = [
    ("class A:\n    signal s()\n", "signal"),
    ("emit s(1)\n", "emit"),
    ("x := task f()\n", "task"),
    ("x := thread a::b\n", "thread"),
]


@pytest.mark.parametrize("src,needle", CANNOT_CROSS, ids=[c[1] for c in CANNOT_CROSS])
def test_a_construct_the_format_lacks_fails_by_name(src, needle):
    with pytest.raises(sexpr.SexprError) as excinfo:
        sexpr.encode(parse(src))
    assert needle in str(excinfo.value)


MALFORMED = [
    (sexpr.node("nosuchkind"), "'nosuchkind is not a node kind"),
    (42, "a node must be a symbol or a list, not a int"),
    (sexpr.node("+", sexpr.node("int", 1)), "'+ takes 2 field(s), not 1"),
    (sexpr.node("int", "text"), "value must be a number"),
    (sexpr.node("str", Symbol("s")), "text must be a str"),
    (sexpr.node("int"), "'int takes 1 field(s), not 0"),
    (sexpr.node("return", sexpr.node("int", 1), sexpr.node("int", 2)),
     "'return takes at most one value"),
    (sexpr.node("define", Symbol("x"), Symbol("int"), NIL), "a type must be"),
    (sexpr.node("cond", sexpr.node("else", sexpr.node("pass")),
                sexpr._pairs([Symbol("a"), sexpr.node("pass")])),
     "else clause must be the last"),
]


@pytest.mark.parametrize("bad,needle", MALFORMED, ids=[str(m[1]) for m in MALFORMED])
def test_a_malformed_tree_says_what_is_wrong(bad, needle):
    with pytest.raises(sexpr.SexprError) as excinfo:
        sexpr.decode(bad)
    assert needle in str(excinfo.value)


def test_string_quoting_round_trips_through_the_evaluator():
    """A `str` decoded and then evaluated yields the characters it came in
    with - which needs quote_string to be the exact inverse of
    eval_string_literal."""
    from wypoc.wyrm_eval_parse_tree import eval_string_literal

    for text in ['plain', 'with "quotes"', "tab\there", "line\nbreak", "back\\slash"]:
        node = sexpr.decode(sexpr.node("str", text))
        assert eval_string_literal(node.value) == text


def test_number_spelling_round_trips_through_the_evaluator():
    from wypoc.wyrm_eval_parse_tree import eval_number_literal

    for value in [0, 41, 2.5, 2.0, 1e100, 0.1]:
        node = sexpr.decode(sexpr.node("float" if isinstance(value, float) else "int",
                                       value))
        assert eval_number_literal(node.value) == value


def test_char_spelling_round_trips_through_the_evaluator():
    from wypoc.wyrm_eval_parse_tree import eval_char_literal

    for codepoint in [97, 10, 32, 0, 0x263A]:
        node = sexpr.decode(sexpr.node("char", codepoint))
        assert eval_char_literal(node.value) == codepoint


def test_encoding_carries_no_source_positions():
    """A tree rebuilt from an s-expression reports at the decorator that
    produced it, so the spans are deliberately not in the format."""
    decoded = sexpr.decode(encode_first("x := 1 + 2"))
    assert all(node.pos is None for node in decoded.walk())

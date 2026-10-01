"""The Scheme s-expression printer (wypoc/sexp_print.py, the D2 form): what
tree dumps, `str()`, `println` and the REPL all print a pair list as."""
import pytest

from wypoc import sexp_print
from wypoc.wyrm_builtins import NIL, Pair, Symbol, _to_str, cons


def plist(*items):
    out = NIL
    for item in reversed(items):
        out = Pair(item, out)
    return out


@pytest.mark.parametrize("value,expected", [
    (plist(1, 2, 3), "(1 2 3)"),
    (NIL, "()"),
    (None, "()"),
    (plist(), "()"),
    (cons(1, 2), "(1 . 2)"),
    (cons(1, cons(2, 3)), "(1 2 . 3)"),
    (plist(1, plist(2, 3), Symbol("a"), "s"), '(1 (2 3) a "s")'),
    (plist(Symbol("define"), Symbol("+"), Symbol("::"), Symbol("$ast")),
     "(define + :: $ast)"),
    (plist(plist(), NIL), "(() ())"),
    (-7, "-7"),
    (1.0, "1.0"),
    (1500.0, "1500.0"),
    (1e20, "1e20"),
    (1.5e-7, "1.5e-7"),
])
def test_write(value, expected):
    assert sexp_print.write(value) == expected


@pytest.mark.parametrize("text,expected", [
    ("plain", '"plain"'),
    ('q"uote', '"q\\"uote"'),
    ("back\\slash", '"back\\\\slash"'),
    ("a\tb\nc\rd", '"a\\tb\\nc\\rd"'),
    ("bell\x07", '"bell\\x07"'),
    ("\x7f", '"\\x7F"'),
    ("é😀", '"é😀"'),
])
def test_string_escapes(text, expected):
    assert sexp_print.quote(text) == expected


def test_an_element_with_no_scheme_spelling_uses_the_fallback():
    assert sexp_print.write(plist([1, 2], {"k": 1})) == '([1, 2] {"k": 1})'
    assert sexp_print.write(plist([1]), other=lambda v: "#") == "(#)"


def test_str_of_a_pair_list_is_the_scheme_form():
    """O1: one printer for pair lists everywhere."""
    assert _to_str(plist(1, plist(2, 3), Symbol("a"), "s")) == '(1 (2 3) a "s")'


def test_nil_on_its_own_is_still_nil():
    """The empty list and nil are one value in wypoc; printed on its own it
    is `nil`, and only inside a list (where it is a list's tail, or an
    absent field) is it `()`."""
    assert _to_str(NIL) == "nil"
    assert _to_str(plist(NIL)) == "(())"

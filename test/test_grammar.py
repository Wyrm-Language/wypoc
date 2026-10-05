"""Parses every sample under wypoc/samples/, failing loudly on any syntax
error.

If wyrm.gram changed, regenerate the parser first:
    .venv/bin/python -m pegen wypoc/wyrm.gram -o wypoc/parser.py -q
"""
import glob
import os

import pytest

from conftest import SAMPLES_DIR
from wypoc.parse import parse

SAMPLE_PATHS = sorted(glob.glob(os.path.join(SAMPLES_DIR, "*.wy")))


@pytest.mark.parametrize(
    "path", SAMPLE_PATHS, ids=[os.path.basename(p) for p in SAMPLE_PATHS]
)
def test_sample_parses(path):
    with open(path) as f:
        src = f.read()
    tree = parse(src)
    assert tree.body is not None


# --------------------------------------------------------------------------
# `$` in names, and the one `$`-name the language keeps for itself.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "reg$0 := 1\n",
    "$total := 1\n",
    "fn f($a, b$c): pass\n",
    "x := a$b + $c\n",
    "class Point$2:\n    slot x$: int = 0\n",
])
def test_a_dollar_is_an_ordinary_identifier_character(src):
    assert parse(src).body


def test_the_pair_list_sigil_still_parses():
    from wypoc import ast_nodes as ast

    tree = parse("x := $[1, 2]\n")
    assert isinstance(tree.body[0].values[0], ast.Pair)


def test_ast_is_an_ordinary_path():
    """`foo::$ast` is a plain `::` path (design syntax.md G7); the evaluator
    resolves the `$ast` segment."""
    from wypoc import ast_nodes as ast

    tree = parse("x := foo::$ast\n")
    value = tree.body[0].values[0]
    assert isinstance(value, ast.Scope) and value.name == "$ast"


def test_another_dollar_name_after_a_scope_is_a_plain_lookup():
    from wypoc import ast_nodes as ast

    tree = parse("x := foo::$line\n")
    value = tree.body[0].values[0]
    assert isinstance(value, ast.Scope) and value.name == "$line"


@pytest.mark.parametrize("src", [
    "$ast = 1\n",
    "x := $ast\n",
    "fn f($ast): pass\n",
    "var $ast: int\n",
])
def test_dollar_names_are_ordinary_names(src):
    """No `$` name is reserved (G7)."""
    assert parse(src).body


# --------------------------------------------------------------------------
# `is not` - the negated type check.
# --------------------------------------------------------------------------

def test_is_not_is_built_as_the_not_of_a_check():
    from wypoc import ast_nodes as ast

    value = parse("x := a is not int\n").body[0].values[0]
    assert isinstance(value, ast.UnaryOp) and value.op == "not"
    assert isinstance(value.operand, ast.TypeCheck)
    assert [t.parts for t in value.operand.types] == [["int"]]


def test_is_not_takes_a_whole_union():
    value = parse("x := a is not int | float\n").body[0].values[0]
    assert [t.parts for t in value.operand.types] == [["int"], ["float"]]


def test_is_not_and_not_is_agree():
    assert str(parse("x := a is not int\n")) == str(parse("x := not a is int\n"))


def test_a_type_named_with_a_not_prefix_is_still_a_plain_check():
    """`not` is a reserved word, so `not_a_type` is one NAME and the check
    is the ordinary positive one."""
    from wypoc import ast_nodes as ast

    value = parse("x := a is not_a_type\n").body[0].values[0]
    assert isinstance(value, ast.TypeCheck)
    assert [t.parts for t in value.types] == [["not_a_type"]]


# --------------------------------------------------------------------------
# Statements need a separator (design syntax.md G0).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "f() g()\n",
    "with x = 1\n",
    "x := a if b else c\n",
    "x := a < b < c\n",
    # A `}` is not a separator: a statement after a brace block needs one
    # (G10's stmt_list).
    "if a { f() } g()\n",
])
def test_juxtaposed_statements_are_a_syntax_error(src):
    with pytest.raises(SyntaxError):
        parse(src)


@pytest.mark.parametrize("src", [
    "f(); g()\n",
    "while c { f(); g() }\n",
    "f := fn():\n    1\ng()\n",
    "x := str(do: 1)\n",
    "if a { f() }; g()\n",
    "x := 1;;; y := 2\n",
    "while c {\n    f()\n\n    # note\n    g()\n}\n",
])
def test_a_statement_ends_at_a_separator_or_its_block(src):
    assert parse(src).body


# --------------------------------------------------------------------------
# `elif`/`else` follow a clause_body (design syntax.md G10).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("src, count", [
    # A one-line block holds one statement and leaves its separator to the
    # enclosing list, so what follows is a statement of its own.
    ("if a: x; y\n", 2),
    ("if a: x\ny\n", 2),
    ("f := fn(x): x\nprintln(1)\n", 2),
    ("for i in xs: f(i); g()\n", 2),
])
def test_a_one_line_block_leaves_its_separator(src, count):
    assert len(parse(src).body) == count


@pytest.mark.parametrize("src", [
    "if a: x; else: y\n",
    "if a: x;\nelse: y\n",
    "if a: x\nelse: y\n",
    "if a { x }\nelse { y }\n",
    "if a { x } else { y }\n",
    "if a:\n    x\nelse:\n    y\n",
    "while c {\n    if a { x }\n\n    else { y }\n}\n",
])
def test_else_follows_a_clause_body(src):
    from wypoc import ast_nodes as ast

    (stmt,) = parse(src).body
    if isinstance(stmt, ast.While):
        (stmt,) = stmt.body
    assert isinstance(stmt, ast.If) and stmt.orelse


def test_elif_chains_mix_block_kinds():
    from wypoc import ast_nodes as ast

    (stmt,) = parse("if a: w\nelif b: x; elif c { y }\nelse: z\n").body
    assert isinstance(stmt, ast.If)
    assert [e.cond.id for e in stmt.elifs] == ["b", "c"]
    assert stmt.orelse


def test_for_else_follows_a_clause_body():
    from wypoc import ast_nodes as ast

    (stmt,) = parse("for i in xs: f(i); else: g()\n").body
    assert isinstance(stmt, ast.For) and stmt.orelse


@pytest.mark.parametrize("src, where", [
    ("x\nelse: y\n", (2, 1)),
    ("if a: x; y\nelse: z\n", (2, 1)),
    ("if foo:\n    stmt; else: bar\n", (2, 11)),
    ("if foo:\n    stmt\n;\nelse:\n    bar\n", (4, 1)),
    ("if a: x;;else: y\n", (1, 10)),
    ("if a { x }; else { y }\n", (1, 13)),
    ("if a { x }\n;\nelse { y }\n", (3, 1)),
    ("if a: b else: c\n", (1, 9)),
    ("for i in xs: f(i) else: g()\n", (1, 19)),
])
def test_a_misplaced_else_is_a_syntax_error(src, where):
    with pytest.raises(SyntaxError) as info:
        parse(src)
    assert (info.value.lineno, info.value.offset) == where
    assert "else" in info.value.msg

"""Imported names share the exporter's binding (project design/modules.md M2).

Lisp semantics: `import m::(x)` and `import m::*` bind the importer's name to
`m`'s own binding, not a copy of its value. A later assignment in `m` is seen
through it, assigning through it is an error (only the defining module
assigns its names, as in a Scheme library), and a re-exported name is the
original binding."""
import pytest

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
        "counter.wy": "count := 0\nfn bump():\n    count = count + 1\n    return count\n",
    })
    yield tmp_path
    clear_module_cache()


def _run(source: str) -> dict:
    ctx: dict = {}
    eval_program(parse(source), ctx)
    return ctx


def _value(ctx, name):
    return ctx[name].value


def test_an_item_import_sees_a_later_assignment(root):
    ctx = _run("import counter::(count, bump)\nbump()\nbump()\nv := count\n")
    assert _value(ctx, "v") == 2


def test_a_wildcard_import_sees_a_later_assignment(root):
    ctx = _run("import counter::*\nbump()\nv := count\n")
    assert _value(ctx, "v") == 1


def test_an_aliased_import_is_the_same_binding(root):
    ctx = _run("import counter\nimport counter::(count as c, bump)\nbump()\nv := c\nw := counter::count\n")
    assert _value(ctx, "v") == 1 and _value(ctx, "w") == 1


@pytest.mark.parametrize("source", [
    "import counter::(count)\ncount = 5\n",
    "import counter::*\ncount = 5\n",
])
def test_assigning_an_imported_name_is_an_error(root, source):
    with pytest.raises(NameError, match="imported from 'counter'"):
        _run(source)


def test_a_reexported_name_is_the_original_binding(root):
    _tree(root, {
        "pkg/__init__.wy": "import counter::(count, bump)\n",
    })
    ctx = _run("import pkg::(count)\nimport counter\ncounter::bump()\nv := count\n")
    assert _value(ctx, "v") == 1


def test_two_wildcards_reaching_one_binding_are_not_ambiguous(root):
    _tree(root, {
        "left.wy": "import counter::*\n",
        "right.wy": "import counter::*\n",
    })
    ctx = _run("import left::*\nimport right::*\nv := count\n")
    assert _value(ctx, "v") == 0


def test_two_wildcards_with_different_bindings_are_ambiguous_when_read(root):
    _tree(root, {
        "left.wy": "count := 1\n",
        "right.wy": "count := 1\n",
    })
    with pytest.raises(NameError, match="ambiguous name 'count'"):
        _run("import left::*\nimport right::*\nv := count\n")


@pytest.mark.parametrize("source", [
    "import counter::*\nfn bump():\n    return 99\nv := bump()\n",
    "import counter::(bump)\nfn bump():\n    return 99\nv := bump()\n",
])
def test_a_definition_replaces_an_import_rather_than_assigning_through_it(root, source):
    """A local definition is layer 1 (doc/addendum.md): it shadows the import
    in this module and leaves the exporter's binding alone."""
    ctx = _run(source + "import counter\nw := counter::bump()\n")
    assert _value(ctx, "v") == 99
    assert _value(ctx, "w") == 1


def test_two_wildcards_passing_on_one_module_agree(root):
    _tree(root, {
        "left.wy": "import counter\n",
        "right.wy": "import counter\n",
    })
    ctx = _run("import left::*\nimport right::*\nv := counter::count\n")
    assert _value(ctx, "v") == 0

"""What wyrm-v0's self-hosted compiler needs to run from source here, so wypoc
can bootstrap it with no binary at all (project design/plans/I-generated-images.md
D10). Backs corelib/std/expand.wy.

wyrm-v0 expands decorators in a throwaway VM that loads a compiled scope image.
This interpreter can't load images, so `__expand` ignores the image and builds the
same scope from the module tree's own import statements, then runs the expander
in-process. The trees it handles are s-expressions, not this interpreter's AST:
that's what a TreeBase box holds here (see sexpr_value).
"""
from wypoc import sexpr
from wypoc.wyrm_builtins import NIL, Symbol, error


def _pair_items(chain):
    while chain is not NIL and chain is not None:
        yield chain.car
        chain = chain.cdr


def _import_nodes(tree):
    """The import statements at the top level of a `(module ...)` tree."""
    for stmt in _pair_items(tree.cdr):
        head = getattr(stmt, "car", None)
        if isinstance(head, Symbol) and head.name in ("import", "import_static"):
            yield stmt


def _scope_module(tree):
    """A module holding only `tree`'s imports, plus the predefined decorators
    (expansion.wy's PREDEFINED_IMPORTS). A host module such as std::io is harmless
    here, unlike in wyrm-v0's expansion VM, so nothing is filtered out."""
    from wypoc import wyrm_eval_parse_tree as ev

    ctx = ev.Scope()
    ev.populate_globals(ctx, name="__expand_scope__")
    ev.eval_program(ev.parse("import wyrm::template::*\n"), ctx)
    for stmt in _import_nodes(tree):
        ev.eval_stmt(sexpr.decode_stmt(stmt), ctx)
    return ev.Module("__expand_scope__", "<expansion scope>", ctx, False)


def expand(tree, _scope_image, entry):
    """`__expand(tree, image, entry)`: `tree` with its decorators expanded by the
    `expand_tree` of module `entry`, or an error value."""
    from wypoc import wyrm_eval_parse_tree as ev

    expander = ev.import_module(str(entry).split("::"))
    return ev.call_value(ev.lookup("expand_tree", expander.ctx), [tree, _scope_module(tree)], {})


def bind_message(scope, recv, name):
    """`bind_message(scope, recv, name)`: the message `name` from module `scope`'s
    table, resolved for `recv` (a tuple is several receivers) and not yet called.
    An error value when `scope` doesn't see it or no overload matches."""
    from wypoc import wyrm_eval_parse_tree as ev

    if not isinstance(scope, ev.Module):
        raise TypeError(f"bind_message: not a module (got {type(scope).__name__})")
    key = name.name if isinstance(name, Symbol) else str(name)
    method = ev.message_table(scope.ctx).get(key)
    if method is None:
        return error("bind_message: no such message in scope")
    receivers = list(recv) if isinstance(recv, tuple) else [recv]
    try:
        return ev.BoundMessage(receivers, ev.resolve_overload(method, receivers))
    except (AttributeError, NameError, TypeError) as exc:
        return error(str(exc))


def parse_tree(source):
    """`source ! __parse_tree()`: the `(module ...)` tree of `source`, the same tree
    wyrm::parser builds (the conformance corpus holds both to it), from this
    interpreter's own parser, which is about 50 times faster here. A message, so wyrm
    code can name it and still compile on a VM without it."""
    from wypoc.parse import parse

    return sexpr.encode(parse(source))


def install(ctx: dict) -> None:
    from wypoc.wyrm_eval_parse_tree import expose_all, register_native_method, tree_box

    expose_all(ctx, __expand=expand, bind_message=bind_message, tree_box=tree_box)
    register_native_method("__parse_tree", parse_tree, ctx)

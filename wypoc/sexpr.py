"""The canonical tree: `wypoc.ast_nodes` <-> pair lists.

This is the bridge a syntax tree crosses in and out of wyrm code. A decorator
receives the tree of what it decorates and answers the tree to compile
instead, `name::$ast` hands one to wyrm code as data, and `wyrm --dump-ast`
prints one. The shapes are the canonical AST, defined outside this repo in
the wyrm project's design notes (`design/ast.md`, with the schema as data
in `conformance/ast/schema.sexp`); `encode` produces them and `decode`
reads them back.

## Shape, in brief

Every list is a pair list. A node is `(head field ... tail ...)`: a fixed
number of fields, then at most one spliced tail. A bare symbol in a root
position (a statement, an operand, an argument) is a name reference; every
other expression or statement is a headed node:

    x := nil           (define x (type auto) (nil))
    f(a, k=1)          (apply f a (kwarg k (int 1)))
    r ! m(1)           (apply (bind_msg r m) (int 1))
    a and b and c      (and a b c)
    if a: b            (cond (a b))
    std::io::println   (:: std io println)

A body (a loop's, a function's, a `cond` clause's, ...) is one form: the
statement itself when there is one, `(do s ...)` when there are more. An
absent optional field is `()`, never `(nil)`; a missing type annotation is
`(type auto)`.

## This AST against the canonical one

wypoc keeps its dataclass AST (one class per construct), which differs in
places; the encoder and decoder below are where that difference lives:

* Statement lists become bodies, and `ExprStmt` has no node: an expression
  in statement position is just the expression. `decode` wraps it again
  wherever a statement is expected (`_stmt`).
* Assignment targets (`NameTarget`/`AttrTarget`/`IndexTarget`) cross as the
  expressions they denote: `x`, `(attr a b)`, `(index a k)`.
* `if`/`elif`/`else` is one `cond`; a `Message` is an `apply` of a
  `bind_msg`, and a `MessageTupleExpr` is the same with a `tuple`
  receiver.
* Types cross as `(type member ...)`: one member for a plain type, more for
  a union (`TypeUnion`), and `(type auto)` for no annotation (None here).
* Strings and numbers hold their raw token text in the AST (see
  ast_nodes.Str/Num), so they are interpreted on the way out and re-spelled
  on the way back in.

`SignalDef`, `Emit`, `ThreadSpawn` and `TaskSpawn` have no canonical shape
until the signals and concurrency research settles one, so they can't
cross (they stay wypoc's own, see `_CANNOT_CROSS`).

No source positions cross either way. A tree rebuilt from an s-expression
reports at the decorator that produced it; its `pos` fields are None, which
every consumer already tolerates (see ast_nodes' module docstring).
"""
from wypoc import ast_nodes as ast
from wypoc.wyrm_builtins import NIL, Pair, Symbol, quote_string

# Binary operator heads, as the grammar spells them (`BinOp.op`).
BINARY = (
    "+", "-", "*", "/", "%", "**", "<<", ">>", "&", "^", "|",
    "<", ">", "<=", ">=", "==", "!=", "<=>", "in",
)

# Unary heads: `UnaryOp.op` -> the head it crosses as.
_UNARY = {"neg": "neg", "pos": "pos", "inv": "~", "not": "not"}
_UNARY_BACK = {head: op for op, head in _UNARY.items()}


class SexprError(Exception):
    """A tree that cannot cross, in either direction: a construct the format
    does not carry, or an s-expression that is not a well-formed node. The
    message names the construct or the mistake; the caller adds the decorator
    and the line (see wyrm_eval_parse_tree.expand_decorated)."""


_CANNOT_CROSS = {
    ast.SignalDef: "a signal has no canonical tree yet",
    ast.Emit: "an emit has no canonical tree yet",
    ast.ThreadSpawn: "a thread spawn has no canonical tree yet",
    ast.TaskSpawn: "a task spawn has no canonical tree yet",
}


# ---------------------------------------------------------------------
# Pair-list plumbing
# ---------------------------------------------------------------------

def _pairs(items) -> object:
    """A proper pair list of `items` - what `$[...]` builds."""
    result = NIL
    for item in reversed(list(items)):
        result = Pair(item, result)
    return result


def node(kind: str, *fields) -> object:
    """`(kind field ...)` - one node."""
    return _pairs([Symbol(kind)] + list(fields))


def _is_nil(value) -> bool:
    return value is NIL or value is None


def _as_list(value, what: str) -> list:
    """A proper list's elements. A Python list is accepted too, since a
    decorator building a tree has no reason to know which it made."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if _is_nil(value):
        return []
    if isinstance(value, Pair):
        out = []
        cursor = value
        while isinstance(cursor, Pair):
            out.append(cursor.car)
            cursor = cursor.cdr
        if not _is_nil(cursor):
            raise SexprError(f"{what} must be a proper list")
        return out
    raise SexprError(f"{what} must be a list, not a {_type_name(value)}")


def _type_name(value) -> str:
    if _is_nil(value):
        return "nil"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Symbol):
        return "sym"
    return type(value).__name__


def _sym(value, what: str) -> str:
    if not isinstance(value, Symbol):
        raise SexprError(f"{what} must be a symbol, not a {_type_name(value)}")
    return value.name


def _opt_sym(value, what: str):
    return None if _is_nil(value) else _sym(value, what)


# ---------------------------------------------------------------------
# Strings and numbers
# ---------------------------------------------------------------------

def spell_number(value) -> str:
    """`value` as the source spelling of a number literal, such that
    eval_number_literal reads back the same int or float. `repr` on a float
    is exact and always carries a `.` or an `e`, which is what tells the two
    apart when the text is read again."""
    return str(value) if isinstance(value, int) else repr(value)


def _spell_char(codepoint: int) -> str:
    """A codepoint as the text of a Char node (see eval_char_literal)."""
    from wypoc.wyrm_eval_parse_tree import CHAR_NAMES

    ch = chr(codepoint)
    for name, value in CHAR_NAMES.items():
        if value == ch:
            return "\\" + name
    return "\\" + ch


# ---------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------

def encode(tree):
    """The canonical tree of one AST node (or of a Program, `(module ...)`).
    A statement list's element crosses exactly as `encode` gives it; a
    body is `encode_body`."""
    if tree is None:
        return NIL
    encoder = _ENCODERS.get(type(tree))
    if encoder is None:
        message = _CANNOT_CROSS.get(type(tree))
        raise SexprError(message or f"{type(tree).__name__} has no canonical tree")
    return encoder(tree)


def encode_body(stmts) -> object:
    """A body (R2): the one statement, or `(do s ...)`. An empty body, which
    only an empty `{}` can spell, is `(pass)`."""
    stmts = list(stmts or ())
    if not stmts:
        return node("pass")
    if len(stmts) == 1:
        return encode(stmts[0])
    return node("do", *[encode(s) for s in stmts])


def encode_type(type_expr) -> object:
    """A type position: `(type auto)` when nothing was written, else
    `(type member ...)` - a member is a bare name, or `(:: a T)` when
    qualified."""
    if type_expr is None:
        return node("type", Symbol("auto"))
    if isinstance(type_expr, ast.TypeUnion):
        return node("type", *[_type_member(t) for t in type_expr.members])
    if isinstance(type_expr, (list, tuple)):
        return node("type", *[_type_member(t) for t in type_expr])
    return node("type", _type_member(type_expr))


def _type_member(type_expr):
    if not isinstance(type_expr, ast.TypeExpr) or not type_expr.parts:
        raise SexprError("a type must be a name, optionally qualified")
    if len(type_expr.parts) == 1:
        return Symbol(type_expr.parts[0])
    return node("::", *[Symbol(p) for p in type_expr.parts])


def _args(args) -> list:
    return [encode(a) for a in args or ()]


def _encode_num(tree: ast.Num):
    from wypoc.wyrm_eval_parse_tree import eval_number_literal

    value = eval_number_literal(tree.value)
    return node("float" if isinstance(value, float) else "int", value)


def _encode_str(tree: ast.Str):
    from wypoc.wyrm_eval_parse_tree import eval_string_literal

    return node("str", eval_string_literal(tree.value))


def _encode_char(tree: ast.Char):
    from wypoc.wyrm_eval_parse_tree import eval_char_literal

    return node("char", eval_char_literal(tree.value))


def _encode_name(tree: ast.Name):
    if tree.id == "nil":
        return node("nil")
    return Symbol(tree.id)


def _flatten(tree: ast.BinOp, op: str) -> list:
    """`a and b and c` parses as a left-leaning chain; it crosses flat."""
    if isinstance(tree, ast.BinOp) and tree.op == op:
        return _flatten(tree.left, op) + _flatten(tree.right, op)
    return [tree]


def _encode_binop(tree: ast.BinOp):
    if tree.op in ("and", "or"):
        return node(tree.op, *[encode(t) for t in _flatten(tree, tree.op)])
    if tree.op not in BINARY:
        raise SexprError(f"the {tree.op!r} operator has no canonical tree")
    return node(tree.op, encode(tree.left), encode(tree.right))


def _encode_unaryop(tree: ast.UnaryOp):
    head = _UNARY.get(tree.op)
    if head is None:
        raise SexprError(f"the unary {tree.op!r} operator has no canonical tree")
    return node(head, encode(tree.operand))


def _scope_segments(tree):
    """`a::b::c` as [a, b, c] when it bottoms out in a plain name, else
    None (e.g. `f()::x`)."""
    parts = []
    cursor = tree
    while isinstance(cursor, ast.Scope):
        parts.append(cursor.name)
        cursor = cursor.obj
    if not isinstance(cursor, ast.Name):
        return None
    parts.append(cursor.id)
    return list(reversed(parts))


def _encode_scope(tree: ast.Scope):
    segments = _scope_segments(tree)
    if segments is None:
        return node("::", encode(tree.obj), Symbol(tree.name))
    return node("::", *[Symbol(s) for s in segments])


def _selector(name: str, module):
    """A message selector: a bare symbol, or `(:: mod name)` for wypoc's
    module-qualified `recv ! mod::name(...)`."""
    if module is None:
        return Symbol(name)
    return node("::", Symbol(module), Symbol(name))


def _encode_message(tree: ast.Message):
    bound = node("bind_msg", encode(tree.obj), _selector(tree.name, tree.module))
    if tree.args is None:
        return bound
    return node("apply", bound, *_args(tree.args))


def _encode_message_tuple(tree: ast.MessageTupleExpr):
    receiver = node("tuple", *[encode(i) for i in tree.items])
    bound = node("bind_msg", receiver, Symbol(tree.name))
    if tree.args is None:
        return bound
    return node("apply", bound, *_args(tree.args))


def _encode_if(tree: ast.If):
    clauses = [_pairs([encode(tree.cond), encode_body(tree.body)])]
    for clause in tree.elifs or ():
        clauses.append(_pairs([encode(clause.cond), encode_body(clause.body)]))
    if tree.orelse is not None:
        clauses.append(node("else", encode_body(tree.orelse)))
    return node("cond", *clauses)


def _encode_for(tree: ast.For):
    orelse = NIL if tree.orelse is None else encode_body(tree.orelse)
    return node("for", Symbol(tree.var), encode(tree.iter), orelse, encode_body(tree.body))


def _values(values):
    """An assignment's or declaration's right-hand side: one expression,
    a `tuple` of several, or () for none."""
    if not values:
        return NIL
    if len(values) == 1:
        return encode(values[0])
    return node("tuple", *[encode(v) for v in values])


def _encode_var_decl(tree: ast.VarDecl):
    if len(tree.targets) == 1:
        target = tree.targets[0]
        return node("define", Symbol(target.name), encode_type(target.type),
                    _values(tree.values))
    targets = [_pairs([Symbol(t.name), encode_type(t.type)]) for t in tree.targets]
    return node("define_values", _pairs(targets), _values(tree.values))


def target_to_expr(target):
    """An assignment target as the expression it denotes."""
    if isinstance(target, ast.NameTarget):
        return ast.Name(target.name)
    if isinstance(target, ast.AttrTarget):
        base = ast.Name(target.base)
        for name in target.attrs:
            base = ast.Attr(base, name)
        return base
    if isinstance(target, ast.IndexTarget):
        return ast.Index(target_to_expr(target.base), target.index)
    raise SexprError(f"{type(target).__name__} is not an assignable target")


def expr_to_target(expr):
    """The inverse of target_to_expr."""
    if isinstance(expr, ast.Name):
        return ast.NameTarget(expr.id)
    if isinstance(expr, ast.Index):
        return ast.IndexTarget(expr_to_target(expr.obj), expr.index)
    if isinstance(expr, ast.Attr):
        attrs = []
        cursor = expr
        while isinstance(cursor, ast.Attr):
            attrs.append(cursor.name)
            cursor = cursor.obj
        attrs.reverse()
        if isinstance(cursor, ast.Name):
            return ast.AttrTarget(cursor.id, attrs)
        raise SexprError("an assignment target's attribute chain must start at a name")
    raise SexprError(f"a {type(expr).__name__} is not an assignable target")


def _encode_assign(tree: ast.Assign):
    targets = [encode(target_to_expr(t)) for t in tree.targets]
    if tree.op == "?=":
        if len(tree.targets) != 1:
            raise SexprError("'?=' has one target")
        return node("if_set", targets[0], _values(tree.values))
    if len(targets) == 1:
        return node("set", targets[0], _values(tree.values))
    return node("set_values", _pairs(targets), _values(tree.values))


def _encode_params(params) -> object:
    out = []
    for p in params:
        if isinstance(p, ast.VarPositional):
            out.append(_pairs([Symbol("*"), Symbol(p.name), encode_type(None)]))
        elif isinstance(p, ast.VarKeyword):
            out.append(_pairs([Symbol("**"), Symbol(p.name), encode_type(None)]))
        else:
            out.append(_pairs([Symbol(p.name), encode_type(p.type), encode(p.default)]))
    return _pairs(out)


def _dispatch(class_target):
    if class_target is None:
        return NIL
    return node("dispatch", *[encode_type(ast.TypeExpr(n.split("::"))) for n in class_target])


def _encode_fn(tree: ast.FnDef):
    return node("fn_def", Symbol(tree.name), _dispatch(tree.class_target),
                _encode_params(tree.params), encode_type(tree.ret), encode_body(tree.body))


def _encode_co(tree: ast.CoDef):
    return node("co_def", Symbol(tree.name), _dispatch(tree.class_target),
                _encode_params(tree.params), encode_type(tree.intype),
                encode_type(tree.ret), encode_body(tree.body))


def _encode_lambda(tree: ast.Lambda):
    return node("lambda", _encode_params(tree.params), encode_type(tree.ret),
                encode_body(tree.body))


def _encode_colambda(tree: ast.CoLambda):
    return node("co_lambda", _encode_params(tree.params), encode_type(tree.intype),
                encode_type(tree.ret), encode_body(tree.body))


def _path(segments):
    if len(segments) == 1:
        return Symbol(segments[0])
    return node("::", *[Symbol(s) for s in segments])


def _encode_import(tree: ast.Import):
    spec = []
    if tree.alias:
        spec.append(node("as", Symbol(tree.alias)))
    elif tree.items:
        spec.append(node("items", *[
            node("item", Symbol(i.name), Symbol(i.alias) if i.alias else NIL)
            for i in tree.items]))
    elif tree.wildcard:
        spec.append(node("all", *[Symbol(n) for n in tree.except_names or ()]))
    return node("import_static" if tree.static else "import", _path(tree.path), *spec)


def _encode_defer(tree: ast.Defer):
    if tree.on is None:
        return node("defer", encode_body(tree.body))
    return node("defer_on", encode_type(tree.on), encode_body(tree.body))


def _encode_decorated(tree: ast.Decorated):
    return node("decorate", Symbol(tree.decorator.name), encode(tree.inner),
                *_args(tree.decorator.args))


def _encode_slot(tree: ast.SlotDef):
    body = NIL if tree.body is None else encode_body(tree.body)
    return node("slot_def", Symbol(tree.name), encode_type(tree.type), encode(tree.default), body)


def _encode_yield(tree: ast.Yield):
    if tree.from_:
        return node("yield_from", encode(tree.value))
    return node("yield", *([] if tree.value is None else [encode(tree.value)]))


def _encode_optional_value(kind):
    def encoder(tree):
        value = getattr(tree, "value", None)
        return node(kind, *([] if value is None else [encode(value)]))
    return encoder


_ENCODERS = {
    ast.Program: lambda t: node("module", *[encode(s) for s in t.body]),
    ast.ExprStmt: lambda t: encode(t.value),
    # literals and names
    ast.Num: _encode_num,
    ast.Str: _encode_str,
    ast.Char: _encode_char,
    ast.Bool: lambda t: node("true" if t.value else "false"),
    ast.Symbol: lambda t: node("sym", Symbol(t.name)),
    ast.EllipsisExpr: lambda t: node("ellipsis"),
    ast.Name: _encode_name,
    # collections
    ast.Tuple: lambda t: node("tuple", *[encode(i) for i in t.items]),
    ast.Array: lambda t: node("array", *[encode(i) for i in t.items]),
    ast.Pair: lambda t: node("list", *[encode(i) for i in t.elements]),
    ast.Dict: lambda t: node("dict", *[_pairs([encode(e.key), encode(e.value)])
                                       for e in t.entries]),
    # operators
    ast.BinOp: _encode_binop,
    ast.UnaryOp: _encode_unaryop,
    ast.TypeCheck: lambda t: node("is", encode(t.value), encode_type(t.types)),
    # application, paths
    ast.Call: lambda t: node("apply", encode(t.func), *_args(t.args)),
    ast.Kwarg: lambda t: node("kwarg", Symbol(t.name), encode(t.value)),
    ast.SpreadPos: lambda t: node("spread", encode(t.value)),
    ast.SpreadKw: lambda t: node("spread_kw", encode(t.value)),
    ast.Message: _encode_message,
    ast.MessageTupleExpr: _encode_message_tuple,
    ast.Attr: lambda t: node("attr", encode(t.obj), Symbol(t.name)),
    ast.Index: lambda t: node("index", encode(t.obj), encode(t.index)),
    ast.Scope: _encode_scope,
    # control
    ast.Do: lambda t: node("do", *[encode(s) for s in t.body] or [node("pass")]),
    ast.If: _encode_if,
    ast.While: lambda t: node("while", encode(t.cond), encode_body(t.body)),
    ast.For: _encode_for,
    ast.Break: _encode_optional_value("break"),
    ast.Continue: lambda t: node("continue"),
    ast.Pass: lambda t: node("pass"),
    ast.Return: _encode_optional_value("return"),
    ast.Yield: _encode_yield,
    ast.Try: lambda t: node("try", encode(t.value)),
    ast.Catch: lambda t: node("catch", encode(t.value), encode(t.handler)),
    ast.Defer: _encode_defer,
    # bindings
    ast.VarDecl: _encode_var_decl,
    ast.Assign: _encode_assign,
    ast.SetIfUnset: lambda t: node("if_set", encode(t.target), encode(t.value)),
    ast.StaticDecl: lambda t: node("static", Symbol(t.name), encode_type(t.type),
                                   encode(t.default)),
    # definitions
    ast.FnDef: _encode_fn,
    ast.CoDef: _encode_co,
    ast.Lambda: _encode_lambda,
    ast.CoLambda: _encode_colambda,
    ast.ClassDef: lambda t: node("class_def", Symbol(t.name), encode(t.base),
                                 encode_body(t.body)),
    ast.ClassExpr: lambda t: node("class_expr", encode(t.base), encode_body(t.body)),
    ast.SlotDef: _encode_slot,
    ast.Import: _encode_import,
    ast.Decorated: _encode_decorated,
    ast.Annotate: lambda t: node("annotate", Symbol(t.key), encode(t.value), encode(t.target)),
}


# ---------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------

# What may stand directly in a statement list. Anything else decoded in a
# statement position is an expression, wrapped in ExprStmt.
STATEMENT_NODES = (
    ast.ExprStmt, ast.VarDecl, ast.Assign, ast.StaticDecl, ast.If, ast.While,
    ast.For, ast.Break, ast.Continue, ast.Return, ast.Pass, ast.Yield,
    ast.Defer, ast.FnDef, ast.CoDef, ast.ClassDef, ast.SlotDef, ast.Import,
    ast.SignalDef, ast.Emit,
)


def is_statement(tree) -> bool:
    """Whether `tree` stands in a statement list as itself. A decoration
    or annotation is one when what it wraps is."""
    while isinstance(tree, (ast.Decorated, ast.Annotate)):
        tree = tree.inner if isinstance(tree, ast.Decorated) else tree.target
    return isinstance(tree, STATEMENT_NODES)


def decode(sexpr):
    """One AST node from its canonical tree: a Program for `(module ...)`,
    otherwise a statement or an expression node as the head says. Raises
    `SexprError` naming the mistake - the kind, or the field that was
    missing or of the wrong shape."""
    return _any(sexpr)


def decode_stmt(sexpr):
    """`sexpr` in a statement position: an expression comes back wrapped in
    ExprStmt."""
    return _stmt(sexpr)


def decode_body(sexpr) -> list:
    """A body as a statement list: `(do s ...)` is its statements, any other
    form is one statement."""
    if isinstance(sexpr, Pair) and sexpr.car == Symbol("do"):
        return [_stmt(s) for s in _as_list(sexpr.cdr, "'do's statements")]
    return [_stmt(sexpr)]


def _stmt(sexpr):
    tree = _any(sexpr)
    if is_statement(tree):
        return tree
    return ast.ExprStmt(tree)


def _expr(sexpr):
    """`sexpr` in an expression position. `if_set` is the one head with a
    statement form (Assign) and an expression form (SetIfUnset)."""
    if isinstance(sexpr, Pair) and sexpr.car == Symbol("if_set"):
        target, value = _expect(_as_list(sexpr.cdr, "'if_set"), 2, "if_set")
        return ast.SetIfUnset(_expr(target), _expr(value))
    return _any(sexpr)


def _opt_expr(sexpr):
    return None if _is_nil(sexpr) else _expr(sexpr)


def _any(sexpr):
    if isinstance(sexpr, Symbol):
        return ast.Name(sexpr.name)
    if not isinstance(sexpr, Pair):
        raise SexprError(f"a node must be a symbol or a list, not a {_type_name(sexpr)}")
    items = _as_list(sexpr, "a node")
    head = items[0]
    if not isinstance(head, Symbol):
        raise SexprError(f"a node's head must be a symbol, not a {_type_name(head)}")
    kind = head.name
    fields = items[1:]
    if kind in BINARY:
        left, right = _expect(fields, 2, kind)
        return ast.BinOp(kind, _expr(left), _expr(right))
    decoder = _DECODERS.get(kind)
    if decoder is None:
        raise SexprError(f"'{kind} is not a node kind")
    return decoder(kind, fields)


def _expect(fields: list, count: int, kind: str) -> list:
    if len(fields) != count:
        raise SexprError(f"'{kind} takes {count} field(s), not {len(fields)}")
    return fields


def _at_most_one(fields: list, kind: str):
    if len(fields) > 1:
        raise SexprError(f"'{kind} takes at most one value, not {len(fields)}")
    return fields[0] if fields else None


def decode_type(sexpr):
    """A type position back into this AST: None for `(type auto)`, a
    TypeExpr for one member, a TypeUnion for more."""
    items = _as_list(sexpr, "a type")
    if not items or items[0] != Symbol("type"):
        raise SexprError(f"a type must be a (type ...) node, not {_type_name(sexpr)}")
    members = [_decode_member(m) for m in items[1:]]
    if not members:
        raise SexprError("'type needs at least one member")
    if len(members) == 1:
        if members[0].parts == ["auto"]:
            return None
        return members[0]
    return ast.TypeUnion(members)


def _decode_member(member) -> ast.TypeExpr:
    if isinstance(member, Symbol):
        return ast.TypeExpr([member.name])
    items = _as_list(member, "a type member")
    if not items or items[0] != Symbol("::"):
        raise SexprError("a type member must be a name or a (:: ...) path")
    return ast.TypeExpr([_sym(s, "a path segment") for s in items[1:]])


def _decode_types(sexpr) -> list:
    """`is`'s type: the member list, as TypeCheck keeps it."""
    tree = decode_type(sexpr)
    if tree is None:
        raise SexprError("'is needs a type")
    return tree.members if isinstance(tree, ast.TypeUnion) else [tree]


def _decode_num(kind: str, fields: list):
    value, = _expect(fields, 1, kind)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SexprError(f"'{kind}'s value must be a number, not a {_type_name(value)}")
    if kind == "int" and not isinstance(value, int):
        raise SexprError("'int's value must be an int")
    if kind == "float":
        value = float(value)
    return ast.Num(spell_number(value))


def _decode_str(kind: str, fields: list):
    text, = _expect(fields, 1, kind)
    if not isinstance(text, str) or isinstance(text, Symbol):
        raise SexprError(f"'str's text must be a str, not a {_type_name(text)}")
    return ast.Str(quote_string(text))


def _decode_char(kind: str, fields: list):
    codepoint, = _expect(fields, 1, kind)
    if isinstance(codepoint, bool) or not isinstance(codepoint, int):
        raise SexprError("'char's codepoint must be an int")
    return ast.Char(_spell_char(codepoint))


def _decode_args(values) -> list:
    """`apply`'s (and `decorate`'s) arguments: expressions, or the
    `kwarg`/`spread`/`spread_kw` marked forms."""
    out = []
    for value in values:
        if isinstance(value, Pair) and isinstance(value.car, Symbol):
            head = value.car.name
            if head == "kwarg":
                name, arg = _expect(_as_list(value.cdr, "'kwarg"), 2, "kwarg")
                out.append(ast.Kwarg(_sym(name, "'kwarg's name"), _expr(arg)))
                continue
            if head in ("spread", "spread_kw"):
                arg, = _expect(_as_list(value.cdr, f"'{head}"), 1, head)
                out.append((ast.SpreadPos if head == "spread" else ast.SpreadKw)(_expr(arg)))
                continue
        out.append(_expr(value))
    return out


def _decode_selector(value):
    """(name, module) from a selector: a symbol, or `(:: mod name)`."""
    if isinstance(value, Symbol):
        return value.name, None
    items = _as_list(value, "a selector")
    if len(items) == 3 and items[0] == Symbol("::"):
        return _sym(items[2], "a selector"), _sym(items[1], "a selector's module")
    raise SexprError("a selector must be a symbol")


def _decode_bind_msg(receiver, selector, args):
    name, module = _decode_selector(selector)
    if (isinstance(receiver, Pair) and receiver.car == Symbol("tuple")
            and module is None):
        items = [_expr(i) for i in _as_list(receiver.cdr, "a tuple")]
        return ast.MessageTupleExpr(items, name, args)
    return ast.Message(_expr(receiver), name, args, module=module)


def _decode_apply(kind: str, fields: list):
    if not fields:
        raise SexprError("'apply needs a callee")
    callee, args = fields[0], _decode_args(fields[1:])
    if isinstance(callee, Pair) and callee.car == Symbol("bind_msg"):
        receiver, selector = _expect(_as_list(callee.cdr, "'bind_msg"), 2, "bind_msg")
        return _decode_bind_msg(receiver, selector, args)
    return ast.Call(_expr(callee), args)


def _decode_bind_msg_node(kind: str, fields: list):
    receiver, selector = _expect(fields, 2, kind)
    return _decode_bind_msg(receiver, selector, None)


def _decode_scope(kind: str, fields: list):
    if len(fields) < 2:
        raise SexprError("'::' needs at least two segments")
    first, rest = fields[0], fields[1:]
    tree = ast.Name(first.name) if isinstance(first, Symbol) else _expr(first)
    for segment in rest:
        tree = ast.Scope(tree, _sym(segment, "a path segment"))
    return tree


def _decode_path(value) -> list:
    if isinstance(value, Symbol):
        return [value.name]
    items = _as_list(value, "an import path")
    if not items or items[0] != Symbol("::"):
        raise SexprError("an import path must be a name or a (:: ...) path")
    return [_sym(s, "a path segment") for s in items[1:]]


def _decode_import(kind: str, fields: list):
    if not fields:
        raise SexprError(f"'{kind} needs a path")
    path = _decode_path(fields[0])
    spec = _at_most_one(fields[1:], kind)
    tree = ast.Import(path, static=(kind == "import_static"))
    if spec is None:
        return tree
    items = _as_list(spec, "an import spec")
    head = _sym(items[0], "an import spec's head") if items else None
    if head == "as":
        alias, = _expect(items[1:], 1, "as")
        tree.alias = _sym(alias, "'as's alias")
    elif head == "items":
        tree.items = []
        for entry in items[1:]:
            entry_items = _as_list(entry, "an import item")
            if not entry_items or entry_items[0] != Symbol("item"):
                raise SexprError("an import's items are (item name alias) forms")
            name, alias = _expect(entry_items[1:], 2, "item")
            tree.items.append(ast.ImportItem(_sym(name, "'item's name"),
                                             _opt_sym(alias, "'item's alias")))
    elif head == "all":
        tree.wildcard = True
        excluded = [_sym(n, "an excluded name") for n in items[1:]]
        tree.except_names = excluded or None
    else:
        raise SexprError("an import spec is (as ...), (items ...) or (all ...)")
    return tree


def _decode_cond(kind: str, fields: list):
    tests = []
    orelse = None
    for i, clause in enumerate(fields):
        items = _as_list(clause, "a cond clause")
        if items and items[0] == Symbol("else"):
            if i != len(fields) - 1:
                raise SexprError("'cond's else clause must be the last")
            body, = _expect(items[1:], 1, "else")
            orelse = decode_body(body)
        else:
            test, body = _expect(items, 2, "cond clause")
            tests.append((_expr(test), decode_body(body)))
    if not tests:
        if orelse is None:
            raise SexprError("'cond needs at least one clause")
        return ast.If(ast.Bool(True), orelse, [], None)
    (cond, body), rest = tests[0], tests[1:]
    return ast.If(cond, body, [ast.ElifClause(c, b) for c, b in rest], orelse)


def _decode_for(kind: str, fields: list):
    var, iterable, orelse, body = _expect(fields, 4, kind)
    return ast.For(_sym(var, "'for's target"), _expr(iterable), decode_body(body),
                   None if _is_nil(orelse) else decode_body(orelse))


def _decode_define(kind: str, fields: list):
    name, type_sexpr, value = _expect(fields, 3, kind)
    target = ast.VarTarget(_sym(name, "'define's name"), decode_type(type_sexpr))
    return ast.VarDecl([target], None if _is_nil(value) else [_expr(value)])


def _decode_define_values(kind: str, fields: list):
    targets_sexpr, value = _expect(fields, 2, kind)
    targets = []
    for entry in _as_list(targets_sexpr, "'define_values's targets"):
        name, type_sexpr = _expect(_as_list(entry, "a define_values target"), 2,
                                   "define_values target")
        targets.append(ast.VarTarget(_sym(name, "a define_values name"),
                                     decode_type(type_sexpr)))
    if not targets:
        raise SexprError("'define_values needs at least one target")
    return ast.VarDecl(targets, _decode_values(value, len(targets)))


def _decode_values(value, count: int):
    """The inverse of `_values`: a `tuple` of exactly `count` values is
    those values (`a, b := 1, 2`); any other expression is one value (to
    destructure, `a, b := f()`)."""
    if _is_nil(value):
        return None
    tree = _expr(value)
    if count > 1 and isinstance(tree, ast.Tuple) and len(tree.items) == count:
        return tree.items
    return [tree]


def _decode_target(value):
    return expr_to_target(_expr(value))


def _decode_set(kind: str, fields: list):
    target, value = _expect(fields, 2, kind)
    return ast.Assign([_decode_target(target)], "?=" if kind == "if_set" else "=",
                      [_expr(value)])


def _decode_set_values(kind: str, fields: list):
    targets_sexpr, value = _expect(fields, 2, kind)
    targets = [_decode_target(t) for t in _as_list(targets_sexpr, "'set_values's targets")]
    if not targets:
        raise SexprError("'set_values needs at least one target")
    return ast.Assign(targets, "=", _decode_values(value, len(targets)))


def _decode_params(sexpr) -> list:
    out = []
    for entry in _as_list(sexpr, "a parameter list"):
        items = _as_list(entry, "a parameter")
        if items and items[0] in (Symbol("*"), Symbol("**")):
            name, _type = _expect(items[1:], 2, items[0].name)
            cls = ast.VarPositional if items[0] == Symbol("*") else ast.VarKeyword
            out.append(cls(_sym(name, "a parameter's name")))
            continue
        name, type_sexpr, default = _expect(items, 3, "parameter")
        out.append(ast.Param(_sym(name, "a parameter's name"), decode_type(type_sexpr),
                             _opt_expr(default)))
    return out


def _decode_dispatch(sexpr):
    if _is_nil(sexpr):
        return None
    items = _as_list(sexpr, "a dispatch")
    if not items or items[0] != Symbol("dispatch"):
        raise SexprError("a dispatch must be a (dispatch type ...) form")
    names = []
    for entry in items[1:]:
        t = decode_type(entry)
        if not isinstance(t, ast.TypeExpr):
            raise SexprError("a dispatch position names one class")
        names.append("::".join(t.parts))
    return names


def _decode_fn_def(kind: str, fields: list):
    name, dispatch, params, ret, body = _expect(fields, 5, kind)
    return ast.FnDef(_decode_dispatch(dispatch), _sym(name, "'fn_def's name"),
                     _decode_params(params), decode_type(ret), decode_body(body))


def _decode_co_def(kind: str, fields: list):
    name, dispatch, params, send, ret, body = _expect(fields, 6, kind)
    return ast.CoDef(_decode_dispatch(dispatch), _sym(name, "'co_def's name"),
                     _decode_params(params), decode_type(send), decode_type(ret),
                     decode_body(body))


def _decode_lambda(kind: str, fields: list):
    params, ret, body = _expect(fields, 3, kind)
    return ast.Lambda(_decode_params(params), decode_body(body), decode_type(ret))


def _decode_co_lambda(kind: str, fields: list):
    params, send, ret, body = _expect(fields, 4, kind)
    return ast.CoLambda(_decode_params(params), decode_type(send), decode_type(ret),
                        decode_body(body))


def _decode_class_def(kind: str, fields: list):
    name, base, body = _expect(fields, 3, kind)
    return ast.ClassDef(_sym(name, "'class_def's name"), _opt_expr(base), decode_body(body))


def _decode_class_expr(kind: str, fields: list):
    base, body = _expect(fields, 2, kind)
    return ast.ClassExpr(_opt_expr(base), decode_body(body))


def _decode_slot_def(kind: str, fields: list):
    name, type_sexpr, init, body = _expect(fields, 4, kind)
    return ast.SlotDef(_sym(name, "'slot_def's name"), decode_type(type_sexpr),
                       _opt_expr(init), None if _is_nil(body) else decode_body(body))


def _decode_decorate(kind: str, fields: list):
    if len(fields) < 2:
        raise SexprError("'decorate needs a selector and a target")
    selector, target, args = fields[0], fields[1], _decode_args(fields[2:])
    decorator = ast.Decorator(_sym(selector, "'decorate's selector"), args, True)
    return ast.Decorated(decorator, _any(target))


def _decode_annotate(kind: str, fields: list):
    key, value, target = _expect(fields, 3, kind)
    return ast.Annotate(_sym(key, "'annotate's key"), _expr(value), _any(target))


def _decode_defer(kind: str, fields: list):
    if kind == "defer":
        body, = _expect(fields, 1, kind)
        return ast.Defer(None, decode_body(body))
    on, body = _expect(fields, 2, kind)
    return ast.Defer(decode_type(on) or ast.TypeExpr(["error"]), decode_body(body))


def _decode_do(kind: str, fields: list):
    if not fields:
        raise SexprError("'do needs at least one statement")
    return ast.Do([_stmt(s) for s in fields])


def _decode_unary(kind: str, fields: list):
    operand, = _expect(fields, 1, kind)
    return ast.UnaryOp(_UNARY_BACK[kind], _expr(operand))


def _decode_logical(kind: str, fields: list):
    if len(fields) < 2:
        raise SexprError(f"'{kind} takes at least two operands")
    tree = _expr(fields[0])
    for operand in fields[1:]:
        tree = ast.BinOp(kind, tree, _expr(operand))
    return tree


def _decode_yield(kind: str, fields: list):
    value = _at_most_one(fields, kind)
    return ast.Yield(None if value is None else _expr(value))


def _one(cls):
    def decoder(kind, fields):
        value, = _expect(fields, 1, kind)
        return cls(_expr(value))
    return decoder


def _optional_one(cls):
    def decoder(kind, fields):
        value = _at_most_one(fields, kind)
        return cls(None if value is None else _expr(value))
    return decoder


def _none(factory):
    def decoder(kind, fields):
        _expect(fields, 0, kind)
        return factory()
    return decoder


def _collection(cls):
    return lambda kind, fields: cls([_expr(i) for i in fields])


def _decode_dict(kind: str, fields: list):
    entries = []
    for entry in fields:
        key, value = _expect(_as_list(entry, "a dict entry"), 2, "dict entry")
        entries.append(ast.DictEntry(_expr(key), _expr(value)))
    return ast.Dict(entries)


def _decode_static(kind: str, fields: list):
    name, type_sexpr, init = _expect(fields, 3, kind)
    return ast.StaticDecl(_sym(name, "'static's name"), decode_type(type_sexpr),
                          _opt_expr(init))


def _decode_catch(kind: str, fields: list):
    value, handler = _expect(fields, 2, kind)
    return ast.Catch(_expr(value), _expr(handler))


def _decode_attr(kind: str, fields: list):
    obj, field = _expect(fields, 2, kind)
    return ast.Attr(_expr(obj), _sym(field, "'attr's field"))


def _decode_index(kind: str, fields: list):
    obj, key = _expect(fields, 2, kind)
    return ast.Index(_expr(obj), _expr(key))


def _decode_is(kind: str, fields: list):
    value, type_sexpr = _expect(fields, 2, kind)
    return ast.TypeCheck(_expr(value), _decode_types(type_sexpr))


def _decode_while(kind: str, fields: list):
    cond, body = _expect(fields, 2, kind)
    return ast.While(_expr(cond), decode_body(body))


def _decode_sym(kind: str, fields: list):
    value, = _expect(fields, 1, kind)
    return ast.Symbol(_sym(value, "'sym's value"))


_DECODERS = {
    "module": lambda kind, fields: ast.Program([_stmt(s) for s in fields]),
    # literals
    "nil": _none(lambda: ast.Name("nil")),
    "true": _none(lambda: ast.Bool(True)),
    "false": _none(lambda: ast.Bool(False)),
    "int": _decode_num,
    "float": _decode_num,
    "str": _decode_str,
    "sym": _decode_sym,
    "char": _decode_char,
    "ellipsis": _none(ast.EllipsisExpr),
    # collections
    "tuple": _collection(ast.Tuple),
    "array": _collection(ast.Array),
    "list": _collection(ast.Pair),
    "dict": _decode_dict,
    # names and paths
    "::": _decode_scope,
    "attr": _decode_attr,
    "index": _decode_index,
    # operators
    "neg": _decode_unary,
    "pos": _decode_unary,
    "~": _decode_unary,
    "not": _decode_unary,
    "and": _decode_logical,
    "or": _decode_logical,
    "is": _decode_is,
    # application
    "apply": _decode_apply,
    "bind_msg": _decode_bind_msg_node,
    # bindings
    "define": _decode_define,
    "define_values": _decode_define_values,
    "static": _decode_static,
    "set": _decode_set,
    "if_set": _decode_set,
    "set_values": _decode_set_values,
    # control
    "do": _decode_do,
    "cond": _decode_cond,
    "while": _decode_while,
    "for": _decode_for,
    "break": _optional_one(ast.Break),
    "continue": _none(ast.Continue),
    "pass": _none(ast.Pass),
    "return": _optional_one(ast.Return),
    "yield": _decode_yield,
    "yield_from": lambda kind, fields: ast.Yield(_expr(_expect(fields, 1, kind)[0]), True),
    "try": _one(ast.Try),
    "catch": _decode_catch,
    "defer": _decode_defer,
    "defer_on": _decode_defer,
    # definitions
    "fn_def": _decode_fn_def,
    "co_def": _decode_co_def,
    "lambda": _decode_lambda,
    "co_lambda": _decode_co_lambda,
    "class_def": _decode_class_def,
    "class_expr": _decode_class_expr,
    "slot_def": _decode_slot_def,
    "decorate": _decode_decorate,
    "annotate": _decode_annotate,
    "import": _decode_import,
    "import_static": _decode_import,
}

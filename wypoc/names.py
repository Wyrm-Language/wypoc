"""Name placement before a module runs (project design/modules.md M2).

A compiler places every name before the code runs: a local, one of the
module's own globals, a builtin, or a member of a module it imports. A name
that is none of these is an error before anything runs, and so is a name two
wildcard imports supply from different bindings (doc/addendum.md, "Wildcard
ambiguity is an error at the point of use"), when the module uses it.

This interpreter has no compile step, so the check is an approximation that
errs towards silence:

- "Defined" is every name any construct binds anywhere in the module - a
  parameter of one function counts for all of them - plus the builtins and
  prelude, the import bindings, and, for each `import m::*`, every name bound
  anywhere in `m` (and what `m` wildcard-imports in turn). Only a name bound
  nowhere is reported, so a typo is caught and a scoping mistake isn't.
- A wildcard dependency that can't be read statically (not found, or not a
  .wy source) turns the undefined-name check off for the module: anything
  might come from it.
- Ambiguity uses the dependencies' top-level definitions, tracked to the
  module that defines each one, so two wildcards reaching one binding
  through re-exports agree. A name the module binds itself anywhere is its
  own (layer 1) and is never ambiguous.
"""
import os

from . import ast_nodes as ast
from . import cache as cache_mod
from . import wyrm_modules
from .parse import parse

# Names a method binds implicitly (design syntax.md G4).
_IMPLICIT = frozenset({"this", "super"})

_tree_cache: dict = {}
_builtin_names: "frozenset | None" = None


class NameCheckError(SyntaxError):
    """A name the check can't place, or an ambiguous one, with its location.
    `str()` is `line:col: message`, like CompileError."""

    def __str__(self):
        return f"{self.lineno}:{self.offset}: {self.msg}"


def builtin_names() -> frozenset:
    """Every name a fresh module scope already holds: the builtins, the
    natives populate_globals installs, and the prelude."""
    global _builtin_names
    if _builtin_names is None:
        from .wyrm_eval_parse_tree import Scope, populate_globals
        scope = Scope()
        populate_globals(scope, name="__check__")
        _builtin_names = frozenset(k for k in dict.keys(scope) if isinstance(k, str))
    return _builtin_names


def _load_tree(file_path: str):
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        return None
    cached = _tree_cache.get(file_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    tree = cache_mod.load(file_path)
    if tree is None:
        try:
            with open(file_path) as f:
                tree = parse(f.read())
        except (OSError, SyntaxError):
            return None
    _tree_cache[file_path] = (mtime, tree)
    return tree


def _import_bindings(node: "ast.Import") -> list:
    """The names an import statement binds in the importing module."""
    if node.wildcard:
        return []
    names = [node.path[0]]
    if node.items is not None:
        names += [item.alias or item.name for item in node.items]
    else:
        names.append(node.alias or node.path[-1])
    return names


def bound_names(tree) -> set:
    """Every name any construct in `tree` binds, at any depth."""
    names = set()
    for node in tree.walk():
        if isinstance(node, ast.VarTarget):
            names.add(node.name)
        elif isinstance(node, (ast.Param, ast.VarPositional, ast.VarKeyword,
                               ast.StaticDecl, ast.SlotDef, ast.SignalDef,
                               ast.FnDef, ast.CoDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.For):
            names.add(node.var)
        elif isinstance(node, ast.Import):
            names.update(_import_bindings(node))
    return names


def _wildcards(tree) -> list:
    return [node for node in tree.walk() if isinstance(node, ast.Import) and node.wildcard]


def _top_level(tree) -> list:
    out = []
    for stmt in tree.body:
        while isinstance(stmt, (ast.Decorated, ast.Annotate)):
            stmt = stmt.inner if isinstance(stmt, ast.Decorated) else stmt.target
        out.append(stmt)
    return out


def _loose_exports(path, roots, seen: set) -> "set | None":
    """Every name module `path` could offer to `import path::*`: anything it
    binds anywhere, plus its own wildcards' offers. None when it can't be
    read statically."""
    resolved = wyrm_modules.resolve_module(list(path), roots)
    if resolved is None or resolved.file is None:
        return None
    if resolved.file in seen:
        return set()
    seen.add(resolved.file)
    tree = _load_tree(resolved.file)
    if tree is None:
        return None
    names = bound_names(tree)
    for node in _wildcards(tree):
        more = _loose_exports(node.path, roots, seen)
        if more is None:
            return None
        names |= more
    return {n for n in names if not n.startswith("__")}


def exports(path, roots, seen: "set | None" = None) -> dict:
    """Module `path`'s top-level exports, as name -> the defining binding
    ("module::name"): its own definitions, and what it imports by item or
    by wildcard, traced to where they are defined. Best effort: what can't
    be read is left out, which only ever hides an ambiguity."""
    seen = set() if seen is None else seen
    key = "::".join(path)
    resolved = wyrm_modules.resolve_module(list(path), roots)
    if resolved is None or resolved.file is None or resolved.file in seen:
        return {}
    seen.add(resolved.file)
    tree = _load_tree(resolved.file)
    if tree is None:
        return {}
    out: dict = {}
    for stmt in _top_level(tree):
        if isinstance(stmt, ast.VarDecl):
            for target in stmt.targets:
                out[target.name] = f"{key}::{target.name}"
        elif isinstance(stmt, (ast.StaticDecl, ast.ClassDef, ast.SignalDef)):
            out[stmt.name] = f"{key}::{stmt.name}"
        elif isinstance(stmt, (ast.FnDef, ast.CoDef)) and not stmt.class_target:
            out[stmt.name] = f"{key}::{stmt.name}"
        elif isinstance(stmt, ast.Import) and stmt.wildcard:
            excluded = set(stmt.except_names or ())
            for name, origin in exports(stmt.path, roots, seen).items():
                if name not in excluded and name not in out:
                    out[name] = origin
        elif isinstance(stmt, ast.Import) and stmt.items is not None:
            dep = exports(stmt.path, roots, set(seen))
            for item in stmt.items:
                dest = item.alias or item.name
                out[dest] = dep.get(item.name, "::".join(stmt.path) + "::" + item.name)
    return {n: o for n, o in out.items() if not n.startswith("__")}


def _function_statics(fn_def) -> set:
    """A function's statics, as the globals wyrm-v0's compiler makes of them."""
    return {f"{fn_def.name}::{node.name}" for node in fn_def.walk() if isinstance(node, ast.StaticDecl)}


def image_exports(path, roots=None, seen: "set | None" = None) -> set:
    """The names module `path`'s image would export under wyrm-v0's compiler, read
    without compiling it: what that compiler needs from `module_exports`, since it
    numbers slots by these names. Wider than `exports`, which is what `import m::*`
    offers here: a module also exports what it binds by importing (M1 rule 6), `__`
    names, and its functions' statics. Generating the images from wypoc and from wyrm-v0
    and comparing them (`embed-check`) is what holds the two to the same answer."""
    seen = set() if seen is None else seen
    resolved = wyrm_modules.resolve_module(list(path), roots)
    if resolved is None or resolved.file is None or resolved.file in seen:
        return set()
    seen.add(resolved.file)
    tree = _load_tree(resolved.file)
    if tree is None:
        return set()
    out: set = set()
    for stmt in _top_level(tree):
        if isinstance(stmt, ast.VarDecl):
            out.update(target.name for target in stmt.targets)
        elif isinstance(stmt, (ast.StaticDecl, ast.ClassDef, ast.SignalDef)):
            out.add(stmt.name)
        elif isinstance(stmt, (ast.FnDef, ast.CoDef)) and not stmt.class_target:
            out.add(stmt.name)
            out.update(_function_statics(stmt))
        elif isinstance(stmt, ast.Import) and stmt.wildcard:
            out.add(stmt.path[0])
            out.update(image_exports(stmt.path, roots, seen) - set(stmt.except_names or ()))
        elif isinstance(stmt, ast.Import):
            out.update(_import_bindings(stmt))
            if stmt.items is not None:
                out.add(stmt.path[-1])
    return out


_DEFINITIONS = (ast.FnDef, ast.CoDef, ast.ClassDef, ast.VarDecl, ast.StaticDecl,
                ast.SlotDef, ast.Decorated, ast.Annotate)


def _references(node):
    """Every node that is read as code, depth-first. Left out: a decorator's
    own arguments, and an expression a decorator wraps (`@accept
    apply_if(...)` in a parser). The decorator receives those as trees and
    reads them as a small language of its own; they aren't names this
    module runs. A decorated definition is still checked."""
    yield node
    for child in node.children():
        if isinstance(child, ast.Decorator):
            continue
        if isinstance(node, ast.Decorated) and child is node.inner \
                and not isinstance(child, _DEFINITIONS):
            continue
        yield from _references(child)


def _error(message: str, node, filename: str) -> NameCheckError:
    pos = getattr(node, "pos", None)
    line, col = (pos[0], pos[1] + 1) if pos else (0, 0)
    return NameCheckError(message, (filename, line, col, None))


def check_names(tree, roots=None, filename: str = "<wyrm>", known=()) -> list:
    """Place every name `tree` reads (see the module docstring). `known` are
    extra names the host binds before the module runs (`__ARGS`). Returns
    the errors, in source order; empty when every name is placed."""
    own = bound_names(tree) | _IMPLICIT | set(known)
    defined = own | builtin_names()
    wildcards = _wildcards(tree)
    undefined_check = True
    offers = []
    for node in wildcards:
        loose = _loose_exports(node.path, roots, set())
        if loose is None:
            undefined_check = False
        else:
            defined |= loose - set(node.except_names or ())
        excluded = set(node.except_names or ())
        offers.append(("::".join(node.path),
                       {n: o for n, o in exports(node.path, roots).items() if n not in excluded}))

    errors = []
    reported = set()
    for node in _references(tree):
        if not isinstance(node, ast.Name) or node.id in reported:
            continue
        name = node.id
        if undefined_check and name not in defined:
            errors.append(_error(f"undefined name {name!r}", node, filename))
            reported.add(name)
            continue
        if name in own or len(offers) < 2:
            continue
        sources = {}
        for source, offered in offers:
            origin = offered.get(name)
            if origin is not None:
                sources.setdefault(origin, source)
        if len(sources) > 1:
            first, second = list(sources.values())[:2]
            errors.append(_error(
                f"ambiguous name {name!r}: supplied by both '{first}::*' and "
                f"'{second}::*'; disambiguate with an explicit import, or qualify "
                f"at the use site", node, filename))
            reported.add(name)
    return errors

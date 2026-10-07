"""WYRM_PATH-based module search-path resolution for wyrm's import system.

Mirrors Python's own import machinery closely enough to be familiar:
  - WYRM_PATH (colon-separated, like PYTHONPATH) lists directories to search,
    checked in order, with wypoc's own corelib/ directory (installed as
    package data) as a final fallback.
  - `import std::io` looks for io.wy (or io/__init__.wy) in the directory
    `std` resolved to, the way Python searches a package's `__path__`.
  - `import std` (a directory) looks for <root>/std/__init__.wy, matching
    Python's package convention. A bare directory with no __init__.wy is a
    namespace package (PEP 420): it holds modules but runs no code, and may
    be spread over several roots. See resolve_module for the full order.

No parsing/evaluation lives here - just figuring out which file (if any) a
`mod::sub::leaf`-style path resolves to under the configured search paths.
Actually loading a module (parsing + eval_program) lives in
wyrm_eval_parse_tree.py, which imports this module (not the other way
around, to avoid a parse/eval <-> path-resolution import cycle).
"""
import os

_WYPOC_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COREPATH = os.path.join(_WYPOC_DIR, "corelib")


_script_root: "str | None" = None
_extra_paths: list = []
_include_paths: list = []


def set_extra_search_paths(paths) -> list:
    """Registers directories to search *before* WYRM_PATH's own entries -
    the REPL's `path` config option (see project.py), resolved to absolute
    paths by the caller. Returns the previous list, the way
    `set_script_root` does, so a caller can restore it."""
    global _extra_paths
    previous = _extra_paths
    _extra_paths = list(paths)
    return previous


def set_include_paths(paths) -> list:
    """Registers `-I` command-line search directories (cli.py) - checked
    even before `_extra_paths` (the config `path` option), and always
    before WYRM_PATH itself, in the order they were given: `wyrm -Ia -Ib
    script.wy` with `WYRM_PATH=c` searches `a`, then `b`, then `c`. Kept as
    given, not resolved to absolute paths, the same way WYRM_PATH's own
    entries are left for resolve_module_file's os.path.join/os.path.isfile
    to resolve against the working directory. Returns the previous list, so
    a caller (tests) can restore it."""
    global _include_paths
    previous = _include_paths
    _include_paths = list(paths)
    return previous


def set_script_root(path: "str | None") -> "str | None":
    """Register the directory a script is being run from, so its own
    neighbours are importable - `import decolib` next to `main.wy`
    resolves without WYRM_PATH being set at all, the same way Python puts a
    script's directory on sys.path. Set by cli.py (and by tests running a
    sample); returns the previous value so a caller can restore it."""
    global _script_root
    previous = _script_root
    _script_root = os.path.abspath(path) if path else None
    return previous


def script_root() -> "str | None":
    """The directory currently registered by `set_script_root`, for a caller
    that wants to put it back afterwards without changing it first."""
    return _script_root


def search_paths(env: dict = None) -> list:
    """The search roots, in order: the `-I` paths registered by
    `set_include_paths` (cli.py's -I option), then the extra paths
    registered by `set_extra_search_paths` (the REPL's `path` config
    option), then WYRM_PATH's entries (colon-separated), then the running
    script's own directory if one is registered (see set_script_root), then
    the default corelib directory as a fallback. Reads `env` (os.environ by
    default) fresh on every call - nothing here is cached at import time -
    so tests can override WYRM_PATH without needing to patch this module."""
    env = os.environ if env is None else env
    raw = env.get("WYRM_PATH", "")
    paths = list(_include_paths) + list(_extra_paths) + [p for p in raw.split(":") if p]
    if _script_root is not None:
        paths.append(_script_root)
    paths.append(DEFAULT_COREPATH)
    return paths


def prelude_path() -> str:
    """corelib/prelude.wy - always-available globals (e.g. `co range(...)`)
    seeded into every scope by populate_globals (wyrm_eval_parse_tree.py),
    independent of WYRM_PATH/search_paths since it isn't reached through an
    `import` statement."""
    return os.path.join(DEFAULT_COREPATH, "prelude.wy")


class Resolved:
    """Where a `mod::sub::leaf` path lives (project design/modules.md M1).

    `kind` is "package" (a directory with `__init__.wy`), "module" (a `.wy`
    file) or "namespace" (directories with no `__init__.wy`, which hold
    modules but have no code). `file` is the `.wy` file to run, None for a
    namespace package. `dirs` is where the path's children are looked up:
    the package's own directory, every directory a namespace package spans,
    and nothing for a module, which can't have children."""

    __slots__ = ("kind", "file", "dirs")

    def __init__(self, kind: str, file: "str | None", dirs: list):
        self.kind = kind
        self.file = file
        self.dirs = dirs

    @property
    def is_package(self) -> bool:
        return self.kind != "module"

    def __repr__(self):
        return f"Resolved({self.kind!r}, {self.file!r}, {self.dirs!r})"


def _resolve_in(dirs, name: str) -> "Resolved | None":
    """One segment, looked up in `dirs` in order, as Python's FileFinder
    does: within a directory a package (`name/__init__.wy`) beats a module
    (`name.wy`), which beats a bare directory. The first directory with a
    package or a module wins outright; bare directories are only a
    namespace package if no directory has either, and then every one of
    them is a portion of it (PEP 420)."""
    portions = []
    for directory in dirs:
        candidate = os.path.join(directory, name)
        init_candidate = os.path.join(candidate, "__init__.wy")
        if os.path.isfile(init_candidate):
            return Resolved("package", init_candidate, [candidate])
        file_candidate = candidate + ".wy"
        if os.path.isfile(file_candidate):
            return Resolved("module", file_candidate, [])
        if os.path.isdir(candidate):
            portions.append(candidate)
    if portions:
        return Resolved("namespace", None, portions)
    return None


def resolve_module(path_segments, roots=None) -> "Resolved | None":
    """Resolve a `mod::sub::leaf` path (path_segments a list of names, e.g.
    ["std", "io"]), or None if it names nothing.

    The first segment is looked up in the search roots; each later one only
    in the directories its parent resolved to, so a regular package's
    children come from that package's own directory, a namespace package's
    from every root it spans, and a module has none."""
    if roots is None:
        roots = search_paths()
    dirs = list(roots)
    resolved = None
    for name in path_segments:
        resolved = _resolve_in(dirs, name)
        if resolved is None:
            return None
        dirs = resolved.dirs
    return resolved


def resolve_module_file(path_segments, roots=None):
    """The .wy file for a `mod::sub::leaf` path, as (file_path, is_package),
    or None if the path names nothing or names a namespace package (which
    has no file). See resolve_module for the rules."""
    resolved = resolve_module(path_segments, roots)
    if resolved is None or resolved.file is None:
        return None
    return resolved.file, resolved.is_package

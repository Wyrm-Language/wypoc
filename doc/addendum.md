# Addendum: wypoc-specific extensions to the base design

The canonical language spec is `doc/language-spec.md` in the `wyrm` reference
implementation, and changes to *that* design belong there, not here. This
file tracks where wypoc's own implementation adds behavior the base spec
doesn't define, or resolves something the base spec leaves open, so the two
don't quietly drift apart without a record of why.

Each section: what wypoc adds, and why it isn't (yet, or ever) in the base
spec.

## Threading (`thread`)

`thread a::b::c` spawns module `a::b::c` in a fresh child **OS process**
(`multiprocessing`, "spawn" start method - not a real thread despite the
keyword), with no dynamic scope reuse: a fresh interpreter, fresh globals,
its own empty module cache. Calls into it (`remote ! name(...)`) are
synchronous over a pair of queues and block the calling side for the full
round trip; a `signal` the child emits is delivered as a proxy `SignalValue`
on the parent, drained opportunistically during the next blocking call (no
background thread drains it eagerly - a signal fired while the parent isn't
inside a blocking call isn't observed until the next one happens).

Only plain values (numbers, strings, lists/dicts, simple class instances)
may cross the queue boundary; anything holding a native resource (open
files, sockets) is not picklable and isn't allowed to appear in a remote
call's arguments or return value. Not enforced - crossing this line fails
with a raw `PicklingError` at the queue, the accepted POC-level failure
mode.

Implementation: `wypoc/wyrm_remote.py` (`RemoteModule`, `spawn_module_process`).

## Tasks (`task`)

`task expr` runs `expr` and evaluates to a `Future` - a placeholder for a
result that some other thread resolves later via `resolve_with`/
`fail_with`. `resolve(fut)` (a builtin) blocks the caller until the future
settles. In this implementation, the only thing that ever actually resolves
a `Future` asynchronously is a `remote ! name(...)` call made from inside a
`task` block; `task` isn't (yet) a general-purpose green-thread/promise
primitive elsewhere in the language.

Implementation: `wypoc/wyrm_eval_parse_tree.py` (`Future`, `_task_stack`,
`ast.TaskSpawn`'s eval case).

## Signals

A class (or a module, for the module-scope case) may declare
`signal name(...)`. Each instance gets its own `SignalValue` - a
subscriber list reached as an ordinary attribute (`obj.name`, or bare
`name`/`this.name` inside a method, same lookup path a slot gets) - seeded
alongside slots at construction time. `obj.name ! connect(cb)` /
`! disconnect(cb)` subscribe/unsubscribe; `emit name(...)` fires it. This
is a Qt-style signal/slot mechanism grafted onto the class system as "a
message on a value with no real class behind it" (the same trick `str`'s
`substr`/`list`'s `append` use), not something the base spec's class model
describes.

A `thread`-spawned module's signals are proxied transparently: the parent
gets its own local `SignalValue` per remote signal name, fed from
`("signal", name, args)` events on the wire (see Threading above).

Implementation: `wypoc/wyrm_eval_parse_tree.py` (`SignalValue`,
`ast.Emit`, `ast.SignalDef`), `wypoc/wyrm_remote.py` (`RemoteModule.signal`).

## Message identity across modules (design decided, not yet implemented)

**Status: agreed design, not yet built** - tracked so the intent survives
until the implementation lands.

Base spec problem: today, each module keeps its own `message_table`
(`name -> Method`), and only `import mod::*` touches it at all - it copies
entries across with a plain overwrite. A plain `import mod` / `import mod
as alias` binds a `Module` value but never makes that module's messages
reachable, qualified or not; two wildcard-imported modules that both extend
a same-named message silently clobber one another instead of merging. None
of this is namespacing on purpose - it's an import path that was never
built for messages, only for plain variables.

Decided replacement, modeled on Common Lisp's package/generic-function
split (symbols carry a home-package identity; `defmethod` always extends
whatever generic function a symbol's value currently is, never creates a
second one, because there is only ever one object per symbol):

- **A message name has one canonical owner.** The first `fn name(...)` /
  `fn [Cls] name(...)` to define a given name, anywhere, creates the
  `Method` object. It's owned by the module that defined it.
- **Importing brings in a reference, not a copy.** `import std::io::*`,
  and `import std::io as io` reached via `io::msg`, both make the *same*
  canonical `Method` object visible under a name - not a merged/copied
  table entry. Since it's the same object, extending it from an importing
  module and extending it from the owning module are the same operation.
- **Extend vs. create is decided by local visibility.** `fn [Cls] name(...)`
  extends the canonical message if `name` already resolves to one in the
  current module's visible scope (declared locally already, or imported).
  If `name` isn't visible yet, this `fn` creates a *new*, locally-owned
  message - even if some unrelated module has a same-named one that was
  never imported. Two libraries can each own an unrelated `draw` message.
- **Name collisions are a hard import-time error.** If two distinct
  canonical messages both resolve to the same unqualified name in one
  module's scope (e.g. two wildcard imports that collide), that name is
  unusable unqualified until disambiguated with `mod::name` at the call
  site - no last-import-wins, no silent merge.

`mod::name` as a message selector (`instance ! rnd::msg()`) is new syntax
needed to make the disambiguation case usable: it resolves `msg` directly
against `rnd`'s canonical message, bypassing whatever's (or isn't) visible
unqualified in the local scope.

## Import cycles are illegal

**Status: implemented.** This is a change to the base design rather than a
wypoc extension - it still belongs in `doc/language-spec.md` in the reference
implementation, and this section is the record until it lands there.

Implementation: `cli.check_imports` (the build-time walk), and
`wyrm_eval_parse_tree.check_import_cycle` over the one import stack the
interpreter keeps.

The base spec doesn't say what a cyclic import means. Because wypoc
publishes a module in the cache before running its body (so `::`
navigation works while a package initialises), a module reached mid-cycle
is observable in a half-initialized state: its globals hold whatever its
body has assigned so far, and which of them that is depends on statement
order in a file the reader may never have opened.

**The import graph must be acyclic.** A cycle is a diagnostic, reported
against the import statement that closes it and naming the whole path, in
Go's shape:

    import cycle not allowed:
        paint imports palette
        palette imports paint

This buys three things:

- **A total initialization order.** A module's body runs after every module
  it imports has finished its own, and exactly once. That is now a guarantee
  the language makes, not an emergent property of who imported whom first.
- **Every name is bindable at import time.** With no cycles there is no
  moment at which a dependency is visible but incomplete, so an importing
  module can bind everything it needs from a dependency the instant that
  dependency's body has run.
- **Publish-before-init changes job.** The mechanism stays, but it stops
  being a way to *survive* cycles and becomes the way to *detect* them: a
  module found in the cache in the initializing state proves a cycle, and
  raises rather than being handed back half-built.

The cost, which is Go's cost too: mutually recursive definitions cannot span
a module boundary. `class A(foo::B)` in one module and `class B(bar::A)` in
another is now a compile error. The remedies are Go's - merge the two
modules, or hoist the shared piece into a third that both import.

Cycle detection lives in one walk: `check_imports` already walks every
import transitively to resolve it to a file. Detection is that walk with a
visiting-stack instead of a flat visited set, so `--check` shares one
implementation with cycle reporting and the tree walker enforces the same
rule at `import` time.

## Wildcard ambiguity is an error at the point of use

**Status: implemented**, except that the diagnostic is raised when the name is
read rather than refused at compile time - see the note at the end of this
section.

Implementation: `wyrm_eval_parse_tree._merge_wildcard_name` and `AmbiguousName`.

The base spec permits `import a::*` and `import b::*` together but never says
what happens when both export `mix`. Copying names into the importing scope
as the imports run would let the **last** import silently win - an artifact
of a data structure (assignment into a dict) rather than semantics anyone
picked.

Neither is right. Silent shadowing by import order means adding an export to
a library can change the meaning of a downstream module that never mentioned
it. Every language with a wildcard import and a compiler that can see through
it - Java, C++, C#, Rust, Haskell - treats the collision as an error at the
point of use, and the outlier that picks a winner silently (Python's
`from m import *`, last wins) is the one its own ecosystem lints against.

**Precedence is layered.** A name resolves through, in order:

1. the module's own definitions, and its named or aliased imports,
2. wildcard imports,
3. builtins.

A collision *across* layers is not an error - the earlier layer wins, so an
explicit import always beats a wildcard and a local definition always beats
both. This is the escape hatch as well as the rule: an ambiguous wildcard
name is disambiguated by naming it explicitly, `import palette::mix`, which
lifts it into layer 1.

**A collision within layer 2 is a diagnostic**, naming both sources:

    ambiguous name 'mix': supplied by both 'palette::*' and 'pigment::*'
        disambiguate with an explicit import, or qualify at the use site

**The error is at use, not at import.** Two wildcard imports that overlap on
names the module never mentions are legal, and must be - otherwise
`import std::*` alongside any second wildcard becomes unusable the moment the
two share a single name. Only a name the module actually references can be
ambiguous.

This matches the rule already recorded above for messages ("Name collisions
are a hard import-time error"), so the two namespaces now fail the same way
for the same reason.

Because import cycles are illegal, the whole dependency graph is known before
any module in it runs, so this *could* be a compile-time diagnostic:
a whole-program build would know each dependency's export set and could
refuse the ambiguous program outright.

**It is not one yet.** A single parsed program never loads a dependency, so
the collision is detected when the name is filled and reported when the name
is read. The observable rule is the one this section describes either way;
what moves, when a whole-program build exists, is only how early the error
arrives.

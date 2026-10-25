# Lazy-Maze: solving path

## 0. The whole plan

The server is `nc <host> 9999` running a maze written in Python 3.15. The maze
"moves": doors and rooms change as you walk, and some rooms execute effects.
The final room runs `exec(state.script)`, where `state.script` is a string you
have been assembling with every step. The plan was:

1. Read the delivered Python project and understand the room/effect model.
2. Realize that Python 3.15 lazy imports are the timing mechanism: the server
   calls `sys.set_lazy_imports("all")` and a filter, so a room module's code
   runs only when something forces the eager import of that module.
3. Model the maze as a graph whose edges change depending on which modules got
   imported (and thus which side effects collected characters).
4. Search a route that reaches the exit with a `state.script` that compiles:
   a string literal followed by `+breakpoint()`.
5. On the server, `breakpoint()` opens PDB before the concatenation type error,
   and PDB is a remote python shell - read the flag file from there.

## 1. The delivered project

Why this step: the archive ships the real source, so the "reverse engineering"
is reading Python instead of disassembling.

`lazy-maze-d040a24eb3619536.tar.gz` contains a full Python project (`release/`):
- `game.py` - the driver; calls `sys.set_lazy_imports("all")`,
  `sys.set_lazy_imports_filter(...)`, tracks the position, and resolves the
  current room module dynamically;
- `maze_state.py` - one global state: `position`, `script`, `controls`,
  `exits`, `room_modules`, `done`;
- `m/__init__.py` - dynamic imports of the room modules;
- `m/x_*.py` - thousands of room modules: walls, import-control rooms, effect
  rooms and one target room;
- `m/x_d03594116a69.py` - the exit room, which runs `exec(state.script)`;
- `runner.py` / `server.py` - the TCP wrapper.

There is no flag in the local files; the flag string is `hitcon{...}`.

## 2. The mechanism: lazy imports as a switch

Why this step: the whole trick is that a room only "happens" when its module is
imported, and the server controls that with the Python 3.15 lazy-import filter.

`game.py` installs `sys.set_lazy_imports("all")` plus a filter. A module like
`m.x_abc` is only *scheduled* while navigating; its actual code (walls, effects,
extra script characters) executes when the import is forced. Which imports get
forced is decided by the filter obtained via `sys.get_lazy_imports_filter()` -
and several control rooms *change the filter* (flip which namespaces load
eagerly). So the same board position can behave differently depending on the
history of imported modules. This turns the maze into a system whose state is
(position, filter-config, `state.script`, wall layout), not just a coordinate.

## 3. Effects: the payload builder

Why this step: the set of rooms that can add characters is small and has
constraints (quotes/backslash rooms are special), so the payload has to be
spelled with the exact reachable effect set.

Effect rooms append to `state.script`:

```python
state.script += chr(...)
```

The target room executes `exec(state.script)`. A payload that is a valid Python
string literal works; anything that is syntactically invalid aborts the exec
before giving control. The collected string must therefore be a complete literal
followed by a statement. The solution payload is:

```python
"JQ\",~"+breakpoint()
```

- `"JQ\",~"` is a valid string literal (the `\"` is an escaped quote);
- `+breakpoint()` is a normal expression;
- `breakpoint()` enters PDB before the `str + NoneType` TypeError would be
  raised.

Inside PDB we run `import os; ...` commands to list files and read the flag;
`solve.py` sends these commands automatically and greps the output for the flag.

## 4. The route search

Why this step: the payload is the constraint; finding a walk that compiles it is
a combinatorial search over a state-dependent graph.

The search keeps track of (position, imported-effect set, filter state, walls)
and tries moves jn the four directions, applying the lazy-import semantics so
that visiting a room first triggers its module (and its effect). The winning
route has 1356 moves, from (19,19) to (37,1), and assembles exactly
`"JQ\",~"+breakpoint()` (see `scripts/expected_code.txt`). The route is
compressed into `scripts/route.json`; every entry carries the direction and the
expected board coordinate, so the client can self-validate each step.

`solve.py` streams all moves to the server in one go and waits for the `(Pdb)`
prompt; the route validation in the client rejects any step whose computed
position differs from the recorded one.

## 5. Verification

Why this step: the archive's own write-up must be tested, not trusted; the local
verifier re-runs the real room modules.

`verify_local.py` unpacks the original release into a temp dir, installs a small
`_lazy_shim` that emulates Python 3.15's `set_lazy_imports_filter` /
`get_lazy_imports_filter` on Python 3.13, and replays all 1356 moves against the
original modules. Expected output:

```text
Steps: 1356 PDB: True Blocked: 0
Script: '"JQ\\",~"+breakpoint()'
Expected: '"JQ\\",~"+breakpoint()'
VERIFIED_LOCAL_PDB
```

This proves the route is geometrically valid and produces exactly the intended
payload and a live `(Pdb)` prompt. It does not prove the remote accepted the
flag; the local test uses no secret.

I also ran `python3 solve.py --host <host> --port 9999` against the remote; the
client connected, streamed the route, reached PDB and read the flag. The flag
value is stored separately and masked in this repository.

## 6. Dead ends (do not repeat)

- **Treating the maze as a static grid.** The walls change on import, so a
  naive BFS finds paths that later become blocked or produce the wrong script.
- **Spelling the payload with normal characters.** The reachable effect rooms
  cannot emit a plain `"breakpoint()"` string; `JQ",~` are exactly the
  characters the reachable rooms can build, and the escaped quote is required
  by the literal.
- **Ignoring the lazy-import filter.** Without emulating which modules are
  eagerly imported, the wall layout and effect timing are wrong and routes
  "randomly" fail on the server.
- **Using the "welcome"/prompt lines as input.** The server expects plain
  movement commands; any extra chat breaks the run.

## One-paragraph version

The maze is a Python 3.15 program whose rooms are modules loaded lazily; which
module actually runs (and changes walls or appends a character to
`state.script`) depends on the import-filter state changed by control rooms. The
exit executes the assembled script, so the goal is a 1356-step route that
compiles the literal `"JQ\",~"` plus `+breakpoint()`; on the server,
`breakpoint()` opens PDB and the flag is read from the process via PDB commands.

## Lessons learned

- New language features are fair game for a challenge server: `sys.set_lazy_imports`
  made the "state" of a maze include which code had actually run.
- When a target executes a string you control, optimize for reaching a debugger
  (`breakpoint()`) instead of a clean RCE - PDB is a REPL already.
- A local verifier that replays the original modules beats trusting a hand-made
  payload; keep the route checkable (direction+coordinate) so a broken route is
  caught client-side before touching the server.
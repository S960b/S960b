# Lazy-Maze (rev)

- **Platform:** HITCON CTF 2026
- **Category:** Rev (Python, lazy-import maze)
- **Points / Solves:** 179 pts
- **Date:** 2026-10-24
- **Remote:** `<host>:9999` (TCP maze server)
- **Flag:** `hitcon{br4nd_n3..._c0ntrol}` (masked)
- **Files:** `assets/lazy-maze-d040a24eb3619536.tar.gz` (original bundle)
- **Description (official):** "I got stuck in the maze, and it seems to be moving :eyes: Help me escape the maze!"

A Python 3.15 maze: thousands of generated room modules (`m/x_*.py`), rooms literally
move and re-wall when you visit them, and exit executes `exec(state.script)` - the
string you have been accumulating with every room you stepped into. The gimmick is
Python 3.15's new `sys.set_lazy_imports` / `sys.set_lazy_imports_filter`, which the
server sets; each room's effects apply only when the module is actually (eagerly)
imported, so finding a route that compiles the right script is a search problem over
the maze plus import timing. The winning payload is the string literal
`"JQ\",~"+breakpoint()`: `breakpoint()` drops into PDB on the server before the
type error, and PDB is a remote python shell.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/solve.py` - TCP client: sends 1356 moves, waits for PDB, reads the flag (stdlib only)
- `scripts/route.json` - the full 1356-move winning route (start (19,19), finish (37,1))
- `scripts/map.json` - reconstructed map and module metadata
- `scripts/expected_code.txt` - the exact script the route must assemble
- `scripts/verify_local.py` - local reproduction of the maze semantics on Python 3.13
- `evidence/verify_local_run.txt` - captured local verification output
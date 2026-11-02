# compuuuter

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** web
- **Task:** https://game.ctfcup.online/tasks/compuuuter
- **Attachment:** `compuuuter.zip`, with the Flask service
- **Flag:** `ctfcup{...}` (masked)

A Flask service that stores numeric dataflow graphs and evaluates them on request. A hidden operation `type = 1337` returns the flag as a little-endian integer, and both upload and compute reject graphs that contain it.

The bug is a time-of-check to time-of-use gap: `compute` runs `guard(nodes)` and then builds a dictionary of references to the same mutable node objects, reading each node's `type` again later. `PATCH` rewrites a node in place with no flag check, and the app runs threaded, so a concurrent patch can turn a constant into `1337` after the guard has passed.

The window is widened deliberately: on 64-bit CPython `hash(n)` is `n mod (2^61 - 1)`, so ids that are multiples of that modulus all collide, and inserting thousands of them into the dict becomes the delay. The recorded run used 6001 nodes and a 0.3 second delay, and hit on the first attempt.

Solver: `scripts/exploit_compuuuter.py`. Service source: `assets/`. Raw run: `evidence/compuuuter_run.log`.

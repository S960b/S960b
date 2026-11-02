# Snake

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** misc / game automation
- **Task:** https://game.ctfcup.online/tasks/snake
- **Attachment:** browser client (saved copy used while reversing the protocol)
- **Flag:** `ctfcup{...}` (masked)

A hex-grid snake with `radius: 4` and `target: 61`. Since a hex board of radius 4 has `3R^2 + 3R + 1` cells, the target is the whole board: the snake has to occupy every cell.

The client speaks plain WebSocket: `{"type":"move","dir":0..5}` out, `{"type":"state", ...}` in. Greedy search to the food traps itself at length 25 to 34, so the working approach is a precomputed Hamiltonian cycle: the body stays a consecutive arc, the head never chooses, and the run ends after about 1040 moves with `won: true` and the flag in `message`.

Scripts: `scripts/hamilton.py` builds the cycle, `scripts/hamilton_bot.py` plays the game.

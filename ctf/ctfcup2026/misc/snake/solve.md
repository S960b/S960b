# Snake: filling a 61-cell hex board

[По-русски](solve.ru.md)

## 0. The game

A hex-grid snake in the browser. The client is one HTML file with inline JavaScript: it opens a WebSocket to `/ws`, sends `{"type":"move","dir":0..5}` and receives `{"type":"state", ...}` with the snake body, the food, the radius, the length and a `target`.

The goal is not a score but a length: the state says `radius: 4` and `target: 61`. A hex board of radius `R` has `3R^2 + 3R + 1` cells, which for `R = 4` is exactly 61. So the target is "occupy every cell".

Why this step: the number 61 is not a tuning parameter, it is the whole board, and that changes the problem from "eat food" to "cover the board without trapping yourself".

## 1. The protocol

No token is required in the handshake in this deployment: the socket upgrade is accepted directly, and the earlier `Invalid token` came from an expired instance, not from a missing header. One move per message, no timer.

Directions are six, and the deltas are ordered:

```text
0:(1,0) 1:(1,-1) 2:(0,-1) 3:(-1,0) 4:(-1,1) 5:(0,1)
```

Why this step: with the exact delta order the client can be driven from a pure Python socket, no browser automation needed.

## 2. Why greedy search loses

The obvious approach is a breadth-first search to the food, with checks that the snake keeps enough free space and can still reach its own tail. It dies consistently at length 25 to 34.

The reason is structural, not a bug: on a dense board, every local decision can close a corridor behind the head. A heuristic that only looks one move ahead cannot see the trap it is building.

Why this step: reproducing the failure tells us the board needs a global ordering, not a better local score.

## 3. The Hamiltonian cycle

If the snake follows a fixed cycle that visits every cell once, three properties hold automatically:

- the body always occupies a consecutive arc behind the head, so a collision is impossible by construction;
- every cell is visited within one lap, so any food placed on the board will be eaten on the current or next pass;
- the head never needs to make a choice, so no heuristic can trap it.

`scripts/hamilton.py` builds such a cycle with a plain depth-first search over the 61 cells, sorting neighbours by remaining degree (the classic Warnsdorff-style ordering). On this board it finds a cycle immediately.

Why this step: a cycle turns a planning problem into a lookup table, which is why it works where BFS failed.

## 4. The bot

`scripts/hamilton_bot.py` keeps the index of the head in the cycle and moves to `cycle[(i + 1) % 61]` every turn, converting the delta into a direction code. Food is eaten whenever the cycle happens to pass over it.

The full run is about 1040 moves, a few laps, and ends with `{"won": true, "length": 61}`. The flag comes in the `message` field of that final state.

Why this step: the bot only needs the current head position, so it survives reconnects and board resets without extra logic.

## 5. Practical notes

- The WebSocket needs `max_size=None`; state messages with a full body grow past the default limit.
- On `alive: false` the bot sends a reset and continues, up to a small retry budget.
- The saved `snake_index.html` is only for reading the protocol; the solver never uses a browser.
- The flag is masked here as `ctfcup{...}`.

## 6. Dead ends

- Greedy BFS with a tail-reachability check: dies at 25 to 34.
- Flood fill without clipping to the board: runs away to infinity on the hexagonal grid.
- A distance helper that returned a huge sentinel for an empty path made the bot oscillate next to the food instead of eating it.

## 7. Lessons

- Read the target as a statement about the board, not as a score.
- When a local search keeps failing at the same length, look for a global invariant instead of tuning weights.
- A Hamiltonian cycle is the standard answer for "cover every cell without self-collision" games.

One paragraph: the board of radius 4 has exactly 61 cells and the target is 61, so the snake must fill the board; a greedy search traps itself around length 30, while a precomputed Hamiltonian cycle makes the body a consecutive arc, so the bot just walks the cycle for about 1040 moves until the server reports the win and returns the flag in the state message.

# Rovny robot (flygammon)

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 642
- **Category:** crypto / blockchain / game automation
- **Task:** https://game.ctfcup.online/tasks/flygammon
- **Attachments:** `flygammon-handout.tgz`, with contract and bot model
- **Flag:** `ctfcup{...}` (masked)

A backgammon-like contract required fifteen consecutive wins against an automated opponent. The difficulty was not finding a simple illegal move. Instead, the game used a predictable VRF whose secret exponent was recoverable with Pohlig-Hellman because the relevant group order was smooth. That let us predict dice for future games and choose favorable game IDs.

We then modeled the opponent's two-ply policy, verified it against the original Rust bot and prepared an on-chain execution plan. The archived research reached an observed streak of 11 before instance expiry; the scoreboard later recorded the challenge submitted. [solve.md](solve.md) carefully distinguishes those two observations.

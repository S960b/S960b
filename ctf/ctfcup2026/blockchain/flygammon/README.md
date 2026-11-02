# Rovny robot (flygammon)

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 642
- **Category:** crypto / blockchain / game automation
- **Task:** https://game.ctfcup.online/tasks/flygammon
- **Attachments:** `flygammon-handout.tgz`, with contract and bot model
- **Flag:** `ctfcup{...}` (masked)

A backgammon-like contract required fifteen consecutive wins against an automated opponent. The weakness was the randomness: `P - 1` was smooth, so Pohlig-Hellman recovered the VRF secret exponent and every future dice roll became predictable.

The opponent's two-ply policy was reimplemented in Python and checked against the original Rust bot (332 of 332 decisions). `skip()` preserved the win streak while unfavorable game ids were discarded, so the plan only had to find fifteen winning ids. After an instance expired at a streak of 11, a second deployment with new parameters was solved from scratch: fifteen wins in about 318 seconds, `streak` 15, `isSolved()` true, flag returned by the server.

The surviving toolchain is published: `scripts/` holds the VRF check, the planner, the Rust parity checker, the legality verifier and both replayers, and `assets/plan_15_first_instance.json` is a complete generated plan. The second-instance plan and the final run's transaction log did not survive; [solve.md](solve.md) says exactly what is missing and why.

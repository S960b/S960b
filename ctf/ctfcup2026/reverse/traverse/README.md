# Traverse

[Русская версия](README.ru.md) | [Research and solving notes](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 207
- **Category:** reverse engineering / FPGA
- **Task:** https://game.ctfcup.online/tasks/traverse
- **Attachments:** `traverse.zip`, with `traverse.bin` and `run.py`
- **Flag:** `ctfcup{...}` (masked)

The given binary was an FPGA configuration, not a normal CPU executable. Its companion Python script provided the loading or execution context. An iCE40 HX1K bitstream can be unpacked into an IceStorm ASCII configuration, exposing logic cells and their routing.

The surviving handoff includes parsing and unpacking scripts plus `chip.asc` and `active.txt`. It documents an iCE40 design with approximately 692 active cells and no active block RAM. The scoreboard recorded this challenge as solved, but the last flag-extraction operation is absent from the preserved notes. [solve.md](solve.md) carefully separates reverse-engineering work from the missing final step.

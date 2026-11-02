# MMM

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** reverse engineering / math
- **Task:** https://game.ctfcup.online/tasks/mmm
- **Artifact:** ELF64 binary from `mmm.zip`
- **Flag:** `ctfcup{...}` (masked)

The binary does not contain the flag as a string. At file offset `0x3220` in `.rodata` it stores 1600 signed 64-bit values, which form a 40 by 40 matrix, and the acceptance test is the eigenvector relation `M v = 1337 v`.

The nullspace of `M - 1337 I` is one dimensional. Scaled by its common denominator 125 the components are printable ASCII, which is the expected input. The binary accepts it (`The seal breaks. Carry what you brought to the gate.`, exit status 0) and rejects any single character change; its embedded SHA-256 comparison confirms the same string.

Extraction and solving are in `scripts/extract_matrix.py`, with the derived table in `scripts/matrix.json`.

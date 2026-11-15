# Matryoshka

[Русская версия](README.ru.md) | [Detailed solution](solve.md)

**Category:** Reverse engineering · **Difficulty:** 1 · **Result:** platform-accepted flag

A stripped ELF launches a nested `stage2` via `memfd_create`. The nested binary checks four independent CRC32 constraints on a 16-byte secret. Inverting their affine GF(2) maps recovers the hexadecimal password; running the stage prints the flag.

**Password (hex):** `c667c58c3b02d6c91b8def4fdd1bc5f3`  
**Flag:** `PP{th3r3_1s_4_b1n4ry_1n_my_b1n4ry?!}`

[Full walkthrough](solve.md) · [Live-session checks](evidence/session-checkpoints.md)

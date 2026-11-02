# MMM

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** reverse engineering / math
- **Task:** https://game.ctfcup.online/tasks/mmm
- **Artifact:** ELF64 binary from `mmm.zip`
- **Flag:** `ctfcup{...}` (masked)

The binary did not simply contain the flag as a string. Its read-only data encoded a 40 by 40 matrix. Solving the eigenvalue problem for 1337 produced an eigenvector with the information needed for the flag. The program then checked the candidate and printed a success message.

This writeup explains why a matrix appears in reversing, how to recover and test the eigenvector, and which parts of the original extraction are no longer available.

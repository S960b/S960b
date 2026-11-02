# filter

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** pwn
- **Task:** https://game.ctfcup.online/tasks/filter
- **Attachment:** `filter.tar.gz`, with `filter.c`, `model.h`, `gen_model.py`, `Makefile`
- **Flag:** `ctfcup{...}` (masked)

A shellcode runner that scores the submitted bytes with a small transformer and executes them only if the score is not positive. Input is a 4-byte little-endian length followed by raw bytes, and the buffer is mapped `PROT_READ | PROT_WRITE | PROT_EXEC`.

The model is a bigram test in disguise: `gen_model.py` builds the weights analytically so that the network matches an oracle for three pairs, `0f 05`, `cd 80` and `0f 34`. Everything else about the code is ignored.

Since the guard reads the bytes once and the mapping stays writable, a 39-byte payload ships `0f 04` plus one instruction that rewrites the next byte to `05` right before the CPU reaches it. The guard reports `score = -10.000` and the process executes a real `execve("/bin/sh")`, which reads the flag from `/flag.txt`.

Solver: `scripts/solve_filter_remote.py` (handles the gateway ticket). Raw run: `evidence/filter_run.log`.

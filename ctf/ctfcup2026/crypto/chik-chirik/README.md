# Chik Chirik

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 229
- **Category:** crypto / misc / audio
- **Task:** https://game.ctfcup.online/tasks/chik-chirik
- **Attachment:** `capture.wav`, plus the `transmitter` binary
- **Flag:** `ctfcup{...}` (masked)

The recording is a digital signal: 1016 frequency chirps with 2048 samples per symbol, eight symbol states, preamble `24355342`. The symbols carry a Reed-Solomon RS(7,3) code over GF(8), and the codewords are then scrambled by a linear xorshift128 generator.

Decoding gives 144 codewords with zero residual errors. The frame law `B = ceil(8(L+4)/3)` fixes the payload at 50 bytes, the two bits leaked per symbol produce a rank-128 linear system for the generator state, and inverting the mixing plus CRC32 yields the flag. The state recovered from the capture is `8bf31bf8 886c0791 8d3e0dc3 1557904c` and the CRC32 is `2a2617cc`.

Solvers are in `scripts/`: `solve_chirp.py` for the WAV and `solve_from_octal.py` for an already demodulated octal stream.

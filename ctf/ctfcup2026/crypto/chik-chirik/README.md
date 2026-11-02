# Chik Chirik

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 229
- **Category:** crypto / misc / audio
- **Task:** https://game.ctfcup.online/tasks/chik-chirik
- **Attachment:** `capture.wav`
- **Flag:** `ctfcup{...}` (masked)

The recording was a structured signal, not ordinary spoken language. The transmitted symbols used a small alphabet and a Reed-Solomon code, then a pseudorandom transformation based on xorshift128. Recovery required several independent stages: demodulation, error-correcting decoding, 128-bit state reconstruction, payload recovery and CRC32 verification.

The exact captured flag bytes were not preserved in the handoff, but the decoder was confirmed on a controlled recording, and the user marked the competition task solved. The technical reconstruction is in [solve.md](solve.md).

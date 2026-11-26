# Twisted Vigenere

[Русская версия](README.ru.md) · [Technical write-up](solve.md)

**GlacierCTF 2026 · Twisted Vigenere · crypto · 50 pts**

**Status: Solved — Flag accepted by platform (participant confirmation).** The accepted flag is redacted in public text as `gctf{...}`.

## Recovered solution

The earlier November 20, 2026 solve session established that the service implements a repeating-key XOR **with the flag itself as the repeating key**, but the other XOR operand comes from Python's `random` module, backed by **MT19937**, rather than cryptographically secure randomness.

The recorded dataset exposed **2,536 output bytes**. The solution modeled the MT19937 internal-state generation and the XOR with the 48-byte periodic secret as a system of linear constraints over GF(2), then applied the generator's state-transition/tempering consistency conditions. The successful recovered period was **48 bytes**. The reconstructed MT state reproduced the recorded **last 40 random bytes**, serving as a cross-check rather than depending solely on an English-looking result.

The accepted output contained literal punctuation (especially `+`), not spaces. The full accepted flag is masked in public Markdown as `gctf{...}`.

**Reproducibility limit:** the original ciphertext, recovered MT state and full original solver were not present in the retrievable chat or Library files during this publication. This document records the verified historical method and cross-checks but does not present a fabricated executable solver. No new encryption or remote run was claimed.


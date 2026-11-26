# Flagpression (crypto / misc)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Author:** sc0peMa3str0
- **Points:** 50
- **Category:** arithmetic coding / encoding
- **Status:** solved; the submitted flag was accepted by the competition platform, reconfirmed by the participant on November 26, 2026

## Task

> One of our members got a bit crazy lately. He draws everywhere this number and talks bogus all the time. Lately, we even saw this piece of paper on his desk.

The challenge gives a handwritten arithmetic-coding model and a 202-digit fractional decimal beginning with `0.000976568521066750...`. The intended task is to understand the symbol alphabet, decode the fraction and recover the flag.

## Files

- [solve.md](solve.md) — detailed investigation, arithmetic model, numerical pitfalls and result.
- [scripts/inspect_decimal.py](scripts/inspect_decimal.py) — exact-rational consistency probe for the supplied decimal and transcribed alphabet (not a complete flag solver).
- [evidence/README.md](evidence/README.md) — provenance and limits of the preserved evidence.

Following the repository convention, the accepted flag is masked here as `gctf{...}`. No original challenge image is redistributed.

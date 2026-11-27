# Frostgate License Manager — GlacierCTF 2026

[Русская версия](README.ru.md) · [Full technical notes](solve.md)

**Author:** xer0 · **Category:** rev / windows · **Points:** 50

> Our new license manager is unbreakable. Find a valid license key. (Tested under Wine & Windows.)

**Status: Solved — key accepted by the GlacierCTF platform (participant confirmation).**

The supplied Windows executable `virt.exe` checks a 40-character license of the form `gctf{...}`. Its license validation employs a virtualized checker. The previous solve conversation recovered exactly **one printable character per position** and identified a **decoy** branch that decrypts to a misleading `wrong{...}` string; the real accepted flag follows the `gctf{...}` format.

The correct flag/license is masked in this public repository as `gctf{...}`, consistent with the other write-ups. The actual value has been recovered from the previous solve conversation and is confirmed accepted.

- [solve.md](solve.md): what the binary and earlier solve history establish.
- [solve.ru.md](solve.ru.md): Russian technical notes.
- [scripts/inspect_handout.py](scripts/inspect_handout.py): dependency-free PE/ZIP section and digest checks.
- [assets/README.md](assets/README.md): exact original handout, layout and checksums.
- [evidence/README.md](evidence/README.md): provenance and the distinction between verified observations and unavailable per-opcode traces.

**Scope:** The earlier conversation retains the successful license and high-level virtualized-checker findings, but the original reversing script or opcode-by-opcode trace is no longer available. This is a documented *historical solved challenge*, with that reproducibility limit stated rather than a fabricated algorithm.

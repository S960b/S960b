# Flagchecker (reverse engineering)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

The supplied archive was named `flagchecker.tar.gz` and contained an ELF executable named `challenge`. The objective was to recover the flag accepted by the executable.

The original solution record reports 1,337 XOR-encoded strings, a single-byte XOR key of `0x12`, and a valid flag associated with entry No. 706. The original program returned `The flag is valid!` for the recovered value.

## Files

- [solve.md](solve.md): detailed reasoning and reproduction steps.
- [scripts/solve.py](scripts/solve.py): reconstructed candidate extractor and optional local verifier.
- [assets/README.md](assets/README.md): status of the original archive.

The archive itself and the original command log could not be recovered when preparing this write-up. The author, points, and verbatim challenge description are not available in the retained record. The solver is a reconstruction, not a claim that the original script survived. The local acceptance result was recorded during the competition.

The complete flag is intentionally masked as `gctf{...}`.

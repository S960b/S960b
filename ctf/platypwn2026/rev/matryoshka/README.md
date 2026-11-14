# Matryoshka

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** Platypwn 2026
- **Category:** rev
- **Difficulty:** 1
- **Materials:** ELF binary `matryoshka`; preserved CRC recovery command
- **Flags:** 1 accepted, exact strings masked (`PP{...}`)

A nested ELF challenge. The important material was hidden in later layers rather than exposed as a clear flag string.

**Core issue:** The recorded solution extracted successive ELF layers and found four CRC32 constraints for four-byte chunks of a 16-byte password. The saved target values are `0x6d795f63`, `0x30306c5f`, `0x70343473`, and `0x77307264`. CRC32 is affine over GF(2), so the bit effects of a 32-bit input can be calculated and inverted with binary Gaussian elimination. This is not a password dictionary attack.

See the [full solving notes](solve.md).

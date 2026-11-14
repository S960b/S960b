# Matryoshka: solving notes

[По-русски](solve.ru.md)

## 1. Task and source material

A nested ELF challenge. The important material was hidden in later layers rather than exposed as a clear flag string. Source files: ELF binary `matryoshka`; preserved CRC recovery command.

## 2. Analysis

The recorded solution extracted successive ELF layers and found four CRC32 constraints for four-byte chunks of a 16-byte password. The saved target values are `0x6d795f63`, `0x30306c5f`, `0x70343473`, and `0x77307264`. CRC32 is affine over GF(2), so the bit effects of a 32-bit input can be calculated and inverted with binary Gaussian elimination. This is not a password dictionary attack.

## 3. Solving procedure

1. Inspect the supplied ELF with `file`, `readelf` and the debugger; extract the next executable layer. The exact layer-extraction commands are missing from the archive.
2. Build a 32 by 32 GF(2) system for each target: `crc32(input) XOR crc32(00000000)` is a linear combination of the 32 individual bit contributions.
3. Solve each system and concatenate the recovered words as little-endian bytes to obtain the 16-byte password.
4. The preserved command reads 36 bytes at offset `0x1b560` from a separately extracted `stage2.bin` and XORs them with the recovered 16-byte password repeated cyclically.
5. Check the decoded text against the accepted result.

## 4. Evidence and result

The archive preserves the exact CRC targets and a Python/GF(2) reconstruction script (`evidence/solve_1.txt`), plus the original ELF. The intermediate `stage2.bin` and extraction transcript were not packaged. The agent reports that the recovered answer was accepted.

Result: **1 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

Layer-extraction commands and the actual stage2 binary are absent. The final XOR step is reconstructed from the saved command, not independently executed here.

## 6. Fix and lesson

CRC32 is a checksum, not a secret-protecting primitive. Linear checks on independently recoverable chunks are reversible; use an authenticated construction for protected data.

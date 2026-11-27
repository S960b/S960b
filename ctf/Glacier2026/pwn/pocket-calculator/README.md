# pocket calculator (pwn / rev)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- Author: leo
- Points: 50 (as shown in the supplied task card)
- Tags: pwn, rev
- Attachment: `pocket-calculator.tar.gz`

## Task

> Become the operator of your pocket calculator!

The program is a static, non-PIE x86-64 ELF implementing an interactive calculator. In RPN mode an arithmetic operation exposes a controlled four-byte memory write. We use this primitive to construct a short ROP chain, redirect the finalization callback array, pivot the stack twice, and read the flag from the guest filesystem.

The flag was recovered during the competition on November 20, 2026. We independently reproduced the local attack on the original executable while preparing this write-up.

## Files

- [solve.md](solve.md): detailed reverse engineering, the exact two-stage stack pivot and ORW chain.
- [scripts/pocketcalc_exploit.py](scripts/pocketcalc_exploit.py): competition solver, with default destination changed to loopback.
- [scripts/local_test_exploit.py](scripts/local_test_exploit.py): test variant reading a synthetic file.
- [assets/README.md](assets/README.md): original archive identity and instructions; large binary artifacts are not duplicated.
- [evidence/](evidence/): masked historical excerpts and a new local verification excerpt.

The real flag and historical endpoint are intentionally masked.

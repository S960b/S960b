# Frostgate License Manager — GlacierCTF 2026

[Русская версия](README.ru.md) · [Full English write-up](solve.md) · [Full Russian write-up](solve.ru.md)

**Author:** xer0 · **Category:** rev / windows · **Points:** 50 · **Status:** Solved, independently reproduced on the original challenge executable.

> Our new license manager is unbreakable. Find a valid license key. (Tested under Wine & Windows.)

The organizer's `virt.tar.gz` contains a Windows x86-64 PE, `virt.exe`. A custom **virtualized, self-modifying** checker in section `.frost0` validates a **40-character** license (the flag). Another 39-character validation branch deliberately leads to a fake `wrong{...}` string.

## Actual solution — recovered and tested

Rather than implement the whole VM, a small Linux C harness loads the **original PE sections at ImageBase `0x100000000`** and invokes two VM-backed functions using the Microsoft x64 calling convention (`ms_abi`):

- `vm_check(c,state,i)` at RVA `0x12340`, compared against the original 40-byte table at RVA `0x19860`.
- `vm_next(c,state,i)` at RVA `0x12410`, advancing the state after the correct character is found.

For each of 40 positions, test all 95 printable ASCII bytes with the current state. Exactly **one match** exists per position. It takes **3,800 comparisons**, rather than trying all 40-character passwords.

The original [`solve_virt.c`](scripts/solve_virt.c) was compiled and executed against the **unmodified organizer `virt.exe`**:

```text
39  }  state=14792a50
FLAG: gctf{...}
final state: 14792a50
```

The original SHA-256 is `ec5620b986ee7102054e676998dedcea7e25f101b0def8d7366f83010bf16ce2`, and all 40 character positions were recovered. The accepted key is masked here according to the repository's public flag-redaction convention; the solver prints the real key at runtime.

## Reproduce

On an **isolated Linux x86-64** machine with GCC and the organizer's archive:

```sh
tar -xzf virt.tar.gz
gcc -O2 -Wall -Wextra -o solve_virt scripts/solve_virt.c
./solve_virt virt/virt.exe
```

The harness runs untrusted machine code from the challenge in its own address space: prefer a disposable CTF VM/container. It does **not** run the PE entry point or require Wine.

Files: [full technical write-up](solve.md), [Russian technical write-up](solve.ru.md), [optional `.frost0` deobfuscation](scripts/deob_virt.py), [organizer source/checksum](assets/README.md), and [evidence](evidence/README.md).

**The previously missing solver/tracing gap is now closed.**

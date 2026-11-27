# Frostgate License Manager — independent solution verification

**Status:** solved and independently reproduced from the original organizer `virt.exe`.

## Source material

- Organizer attachment `virt (1).tar.gz`, containing the original Windows PE and checksum manifest.
- Original solved-conversation document `virt_writeup.md` with native `solve_virt.c`, `deob_virt.py` and bilingual method explanation.
- Previous documented flag accepted by the participant's GlacierCTF account.

## Local verification — 27 November 2026

The exact original `virt.exe` SHA-256 was verified:

```text
ec5620b986ee7102054e676998dedcea7e25f101b0def8d7366f83010bf16ce2
```

Extracted the **original** C solver from `virt_writeup.md`, compiled it with:

```sh
gcc -O2 -Wall -Wextra -o solve_virt solve_virt.c
```

and ran it against the unmodified PE. It recovered a **unique printable ASCII character for each of 40 positions**, printed the same accepted flag as the earlier session and exited successfully:

```text
32  u  state=e48e468d
33  l  state=d4251a55
34  l  state=83976f23
35  y  state=49489839
36  _  state=eece5548
37  u  state=ae9b56a2
38  2  state=118eb4f8
39  }  state=14792a50
FLAG: gctf{...}
final state: 14792a50
```

Separately the original `deob_virt.py` was executed against the same PE:

```text
section RVA: 0x29000
counts by patch width: {1: 31, 2: 43, 4: 339}
total patches: 413
saved: frost_patched.bin (inspection only)
```

These are actual runs, not claims inferred from the accepted flag's spelling.

## Reproduction

```sh
tar -xzf virt.tar.gz
gcc -O2 -Wall -Wextra -o solve_virt scripts/solve_virt.c
./solve_virt virt/virt.exe
python3 scripts/deob_virt.py virt/virt.exe
```

The C harness does not execute the Windows entry point or require Wine but does run machine code from the original `virt.exe` in process memory; use a disposable isolated CTF environment.

The full flag is not embedded in the public Markdown or solver. It is reconstructed at runtime and already confirmed accepted by the participant.

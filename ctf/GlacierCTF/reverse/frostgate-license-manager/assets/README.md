# Frostgate License Manager — original organizer archive

Archive: `virt (1).tar.gz` (organizer name `virt.tar.gz`).

```text
virt/virt.exe       154624 bytes
virt/sha256sum          77 bytes
```

The original `virt.exe` SHA-256 is:

```text
ec5620b986ee7102054e676998dedcea7e25f101b0def8d7366f83010bf16ce2
```

The organizer checksum was checked against the exact uploaded executable in the current session and matched.

It is an x86-64 Windows PE32+ executable, preferred base `0x100000000`, `SizeOfImage=0x2d000`, original entry VA `0x1000016a0`. Besides ordinary sections, it contains `.frost0` at RVA `0x29000`. The `vm_check` function is at RVA `0x12340`, `vm_next` at `0x12410`, and the 40-byte target table at RVA `0x19860` (file offset `0x18260`).

The original binary is retained as the uploaded organizer attachment; it is **not redistributed** by this repository. The [flag-independent solver](../scripts/solve_virt.c) reads the original executable itself, maps sections into a private memory image and executes only the VM functions required for its calculations.

The optional [static deobfuscator](../scripts/deob_virt.py) found 413 RIP-relative self-patches in the original `.frost0` section: `339` four-byte, `43` two-byte and `31` one-byte. It creates an *inspection-only* copy and never changes the original PE.

The earlier repo contained only `inspect_handout.py`; the recovered original solve material now provides the C VM oracle and the `.frost0` inspector. Both were retrieved from the archived solution and checked on this exact binary.

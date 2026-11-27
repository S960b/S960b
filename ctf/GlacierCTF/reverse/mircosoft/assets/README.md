# mircosoft — organizer handout and MiniDump layout

Official participant-uploaded `mircosoft (1).tar.gz` (SHA-256 `25705c846426a9608327848aec9cb1f95f77386dfc27270f4da7d9eb22e5c6f4`).

```text
mircosoft/admin_machine.dmp  50328 bytes
mircosoft/license.bin          104 bytes
mircosoft/mircosoft.exe       27136 bytes
mircosoft/sha256sum             248 bytes
```

The organizer `sha256sum -c sha256sum` reports **OK on all three binary inputs**.

| File | SHA-256 |
| --- | --- |
| `admin_machine.dmp` | `a51edf96a395692dabfb67b654f4eb3f4742dd483da8d7d3d49579a369d2d5e4` |
| `license.bin` | `56b71ff7a851d4b7c074637cd1a96f458b32e2e306754b788c01af8fe8b89891` |
| `mircosoft.exe` | `5412f535d238bdf2418f179044dcd2ee25c6cc3d60d2669196bc9e5131f99712` |

## MiniDump

The file begins `MDMP` and contains **five streams**. MemoryListStream type **5** has **seven regions**. Parsing is implemented in [scripts/extract_minidump.py](../scripts/extract_minidump.py).

| Region | Virtual base | Size |
| --- | --- | --- |
| 0 | `0x7ffe0000` | 4096 |
| 1 | `0x1c000000000` | 8192 |
| 2 | `0x20000000000` | 16384 |
| 3 | `0x21000000000` | 8192 |
| 4 | `0x22000000000` | 4096 |
| 5 | `0x7ff000000000` | 4096 |
| 6 | `0x7ff000001000` | 4096 |

Hardware fingerprint extraction offsets (in **regions**, not in the raw `.dmp`):

```text
CPUID brand       region0[0x274:0x2a4]  48 bytes
Volume serial     region4[0x048:0x04c]   4 bytes
MachineGuid       region1[0x500:0x524]  36 bytes
SMBIOS UUID       region2[0x055:0x065]  16 bytes
MAC address       region3[0x198:0x19e]   6 bytes
```

Concatenation order: **CPUID || volume serial || MachineGuid || SMBIOS UUID || MAC**. Computed SHA-256:

```text
9887f856a75c76842491cd7fff0ee47410c3dc97cebeb71c8cc6db624b24c238
```

The `license.bin` header stores a 16-byte MAC at `[8:24]`, 8-byte nonce `[24:32]`, and 64-byte encrypted data `[32:96]`.

The original archive is unaltered in the conversation attachment. The unpacked PE is an **analysis product**, not an organizer-distributed file. It is now **fully reproducible from the original** `mircosoft.exe` using [scripts/unpack.py](../scripts/unpack.py): 18,432 bytes, SHA-256 `9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80`. Its `.data` at file offset `0x3e00` contains the four 16-byte license keys. End-to-end reproduction: `python3 scripts/reproduce.py mircosoft.tar.gz`; this was checked locally with the supplied organizer files and returns the accepted flag.

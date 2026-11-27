# mircosoft — GlacierCTF 2026

[Русская версия](README.ru.md) · [Technical write-up](solve.md)

**Category:** rev / Windows · **Points:** 50 · **Status:** solved — accepted by the platform (participant confirmation).

This challenge contains a protected Windows launcher (`mircosoft.exe`), a machine-bound license (`license.bin`), and a **Windows MiniDump** of the administrator machine (`admin_machine.dmp`). The original solve recovered the embedded PE, extracted five hardware identifiers from the dump, reproduced the custom MAC, derived the stream key and decrypted the license.

**Accepted flag:** `gctf{...}` (redacted in the public repository).

## Recovered challenge artifacts

The real organizer handout `mircosoft (1).tar.gz` was checked and all three organizer SHA-256 checks passed. We independently parsed the seven memory ranges in the MiniDump and confirmed the exact offsets consumed by the saved original final solver.

| Organizer file | Size |
| --- | ---: |
| `mircosoft.exe` | 27,136 bytes |
| `license.bin` | 104 bytes |
| `admin_machine.dmp` | 50,328 bytes |

## Contents

- [solve.md](solve.md) — full EN technical explanation, checksum/MAC and TEA-like license decryption.
- [solve.ru.md](solve.ru.md) — full RU write-up.
- [scripts/solve.py](scripts/solve.py) — **original script from the successful November 20 solve**, including MAC calculation and final decryption (uses locally unpacked PE).
- [scripts/extract_minidump.py](scripts/extract_minidump.py) — new stdlib-only extraction of all MiniDump memory regions.
- [assets/README.md](assets/README.md) — original file sizes, hashes and byte offsets.
- [evidence/README.md](evidence/README.md) — exact reproduction scope and unavailable original unpacker.

**Reproduction scope:** The original final `solve.py` is preserved verbatim and the MiniDump extraction is reproducible from the handout. The earlier session's separately unpacked `unpacked_pe.bin` and its unpacking helper were not included in the organizer archive or recoverable old files, so a new end-to-end unpacking claim would be misleading. The accepted flag and successful checksum validation are recorded from that prior session.

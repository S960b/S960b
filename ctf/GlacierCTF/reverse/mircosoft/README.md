# mircosoft — GlacierCTF 2026

[Русская версия](README.ru.md) · [Full English write-up](solve.md) · [Combined EN/RU](WRITEUP_RU_EN.md)

**Category:** rev / Windows · **Points:** 50 · **Status:** Solved, independently reproduced from the original handout.

An obfuscated Windows PE launcher hides four DRM keys; the supplied administrator MiniDump provides the machine fingerprint. Reversing the loader, reconstructing a nested PE, rebuilding the machine-bound license MAC, and decrypting an eight-block XTEA-derived stream recovers the platform-accepted flag.

**The previous missing intermediate has been recovered.** The original solve conversation supplied [`scripts/unpack.py`](scripts/unpack.py), a real static PE unpacker. It was tested against the original organizer tarball:

```text
unpacked_pe.bin: 18432 bytes
SHA256: 9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80
Calculated license MAC: 8305d5d5c5316f76bd27ddbbc5221f56
Stored license MAC:     8305d5d5c5316f76bd27ddbbc5221f56
Flag: gctf{...} [accepted, redacted in public text]
```

## Run end to end

```sh
python3 scripts/reproduce.py /path/to/mircosoft.tar.gz
```

Requires Python 3.10+ and the original organizer tarball, **no Wine, Windows, or third-party Python modules**.

This invokes the original [three-stage PE unpacker](scripts/unpack.py), [MiniDump extractor](scripts/extract_minidump.py), and the [historical final decryption algorithm](scripts/solve.py) in a temporary directory, verifying the embedded PE hash and license MAC.

Read [solve.md](solve.md) for a step-by-step technical write-up, [evidence](evidence/README.md) for independent verification, and [scripts](scripts/README.md) for commands and inputs. The full accepted flag is retained in the solve conversation and intentionally masked here.

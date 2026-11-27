# mircosoft — end-to-end independent verification

The missing PE-unpacking step was recovered from the participant's **original solve conversation** and verified with the organizer's `mircosoft (1).tar.gz` in this session.

## Checks completed

| Stage | Independent result |
| --- | --- |
| Organizer package | `mircosoft.exe`, `license.bin`, `admin_machine.dmp`; SHA-256 matched organizer checks |
| Three-stage PE unpacker | 72×256-byte inverse shuffle; rotate/subtract; XOR feedback |
| `unpacked_pe.bin` | 18,432 bytes; SHA-256 `9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80` |
| `.data` keys | Four 16-byte keys found at file offset `0x3e00` |
| MiniDump | Seven memory regions extracted |
| Hardware fingerprint | 48+4+36+16+6=110 bytes |
| Header MAC | `8305d5d5c5316f76bd27ddbbc5221f56` |
| Recomputed MAC | `8305d5d5c5316f76bd27ddbbc5221f56` — exact match |
| Decryption | Accepted `gctf{...}` flag in plaintext; zero padding after closing brace |

Key bytes recovered from the resulting **unpacked PE**:

```text
K0 67e6096a85ae67bb72f36e3c3af54fa5
K1 7f520e518c68059babd9831f19cde05b
K2 982f8a4291443771cffbc0b5a5dbb5e9
K3 5bc25639f111f159a4823f92d55e1cab
```

Intermediate values from the successfully rerun final decryption:

```text
state(cpuid)   6e75487e27f3f55f92a66707fa322b63
state(reg)     9df5ab2c698825c8b3ea6b1bd1ab63fa
state(uuidnic) 55e8a940fa964f9fe1c996e31f338143
derived key    3b8652afcde7de87294a383867f1a9f7
```

## One-command reproduction

```sh
python3 scripts/reproduce.py mircosoft.tar.gz
```

The runner checks the unpacked PE SHA-256, matches the license MAC, and checks the output flag framing. This is a **fully offline** reproduction from the organizer files; no secret source file, arbitrary hardcoded flag, previous intermediate binaries, Windows API, or network access is needed.

The full flag string is omitted in public documentation, following this repository's prior convention, but is output by the solver.

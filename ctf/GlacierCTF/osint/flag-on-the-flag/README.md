# Flag on the flag (OSINT)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- Author: LosFuzzys
- Points: 50
- Difficulty: easy
- Flag format: `LosFuzzys{.*}`

## Task

The retained challenge card said that the team brings its own lucky flag. The object to investigate was therefore not a service or binary but the LosFuzzys team flag/banner itself.

The solve was pure OSINT: find a sufficiently clear photograph of the purple LosFuzzys banner, notice the hexadecimal-looking byte string printed along its lower edge, decode it with XOR `0x42`, and recover the hidden suffix. A fabric fold hid the final byte in the first usable photos, which caused the first submitted candidate to be one character short.

## Files

- [solve.md](solve.md): the complete search and decoding path.
- [scripts/decode_banner.py](scripts/decode_banner.py): tiny XOR helper for transcribed hex bytes.
- [evidence/README.md](evidence/README.md): what was actually retained and which public source independently shows the banner.

The accepted competition flag is intentionally masked in this repository.

# Cold Storage — GlacierCTF 2026

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

**Author:** ansa · **Points:** 50 · **Tags:** datetime, cryptography, web

> Ice VII is pretty cool.

Historical launcher: `nc challs.glacierctf.com 13379`; organizer file: `cold-storage.tar.gz`.

**Solved — flag accepted by the CTF platform (participant confirmation).**

## Vulnerability

The web service uses AES-128-GCM for fixed-length 32-byte records and derives its 96-bit nonce from a `Temporal.Instant` timestamp. Two different ISO timestamp strings, `1972-12-31T23:59:59Z` and `1972-12-31T23:59:60Z`, normalize to the same instant and nonce. The code checks duplicate timestamps **as strings**, so the second value passes the one-time `/api/import` restriction.

One imported known plaintext and one public archived ciphertext share the same key and nonce. With the first ciphertext blocks equal, their authentication tags reveal the GHASH key `H`. Then we derive the GCM keystream, forge a valid tag for `restricted|Ice-VII`, and submit to `/api/open`. The successful remote response returns the real flag (redacted in the public repository).

## Files

- [solve.md](solve.md) — English technical write-up and cryptographic derivation.
- [solve.ru.md](solve.ru.md) — Russian technical write-up.
- [scripts/solve.py](scripts/solve.py) — portable standalone solver, with explicit `--url` or `--spawn`.
- [scripts/test_offline.py](scripts/test_offline.py) — offline algebra self-test against the preserved import and archive records.
- [assets/README.md](assets/README.md) — organizer source and checksum.
- [evidence/README.md](evidence/README.md) — successful log and investigation provenance.

The two user-supplied evidence ZIPs contain identical files. No live service requests were made to prepare this write-up.

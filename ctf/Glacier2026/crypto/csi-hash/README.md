# CSI-HASH — GlacierCTF 2026

[Русская версия](README.ru.md) · [GlacierCTF write-ups](../../README.md)

**Author:** LosFuzzys · **Points:** 50 · **Tags:** PQC, hash

> Everyone fears about quantum-computers breaking public-key primitives but no one thinks about hash functions. To fix this gap we introduce the isogenie based hash function CSI-HASH.

**Status: Solved — flag accepted by platform (participant confirmation).**

Historical service: `nc challs.glacierctf.com 13378`. Original handout: `csi-hash.tar.gz`.

## What broke

The 32 bytes of a message are used as exponents in a CSIDH-like action by 32 small split prime ideals. The hash is an elliptic-curve isomorphism-class representative; the action admits nontrivial integer relations in the quadratic order class group.

The research initially used PARI/GP, class-group logarithms and LLL to discover such relations, then verified them in the **correct non-maximal quadratic order**. The exceptionally simple winning relation was **(-3,-3,...,-3)**: subtract 3 from every byte of the original 32-character random challenge. The result is different, printable, still 32 bytes, and the live service confirms it hashes identically.

## Files

- [solve.md](solve.md) — full technical derivation, class groups and LLL.
- [solve.ru.md](solve.ru.md) — Russian version.
- [scripts/solve.py](scripts/solve.py) — **minimal working second-preimage solver**, standard Python, no Sage needed.
- [scripts/verify_relation.py](scripts/verify_relation.py) — offline check using archived class-group logarithms.
- [scripts/README.md](scripts/README.md) — full original research scripts retained in the agent's evidence ZIP.
- [assets/README.md](assets/README.md) — original Sage implementation and handout checksum.
- [evidence/README.md](evidence/README.md) — successful remote transcript and verification notes.

The original long PARI/LLL scripts and `csi_logs.json` remain in `glacierctf_csi-hash_materials.zip`. Full flag is masked in public GitHub in accordance with this repository's write-up convention.

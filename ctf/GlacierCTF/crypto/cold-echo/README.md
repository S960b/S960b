# Cold Echo — GlacierCTF 2026

[Русская версия](README.ru.md) · [Competition index](../../README.md)

**Category:** crypto · **Author:** LosFuzzys · **Points:** 50 · **Status:** Solved — flag accepted by platform (participant confirmation).

> We lost the reactor's calibration profile after the cold-start test. The test readings and the receipt are still there. Can you recover the profile that was signed?

**Handout:** `cold-echo.tar.gz` (2271 bytes; SHA-256 `a0776d87b60944a1e6d0919012dc4a759212f2d08c015c46d6036f34bc650877`).

## Method

The archive contains 42 field measurements of `P(u)P(u⁻¹)` for an unknown degree-42 polynomial `P` over `F_p`, along with normalization `P(1)=1`, one direct value `P(89)`, and an ECDSA P-256 signature on a canonical receipt containing every polynomial coefficient.

Reconstruct the reciprocal polynomial `S(X)=P(X)X^42P(1/X)` using a **42×43 modular linear system** plus `S(1)=1`. Factor its 84 roots, pair each `r` with `r⁻¹`, then solve the 42 binary choices by **meet-in-the-middle**. Exactly one of 243 candidates has a valid ECDSA signature. Hash the signed receipt, XOR its SHA-256 pad with `flag_mask_b64`, and URL-safe-base64 encode the result.

The result is the platform-accepted `gctf{...}` flag, masked in this public repository.

## Contents

- [solve.md](solve.md) — complete derivation and failure analysis.
- [solve.ru.md](solve.ru.md) — Russian write-up.
- [scripts/](scripts/) — original Python pipeline, from modular Gaussian elimination to ECDSA verification.
- [assets/README.md](assets/README.md) — organizer source and receipt format.
- [evidence/README.md](evidence/README.md) — retained intermediate data, repeat run and provenance.

This is an **offline** cryptographic puzzle: no CTF server connection is needed. Original handout, numerical checkpoints and the original final solver are preserved in the uploaded `glacierctf_cold-echo_materials.zip`.

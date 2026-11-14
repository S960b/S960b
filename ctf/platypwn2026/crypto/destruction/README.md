# DEStruction

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** Platypwn 2026
- **Category:** crypto
- **Difficulty:** 2
- **Materials:** `server.py`, `encryption.py`, Dockerfile, compose; submission command records
- **Flags:** 2 accepted, exact strings masked (`PP{...}`)

An improvised GCM construction based on 3DES with 64-bit blocks and only a two-byte authentication tag. The service has separate verification conditions for two flags.

**Core issue:** `DES3_GCM` defines `H=DES3_K(0^64)`, a custom `GHASH(..., blen=64)` and `tag=(GHASH(H, AD, ciphertext) XOR E_K(Y0))[:2]`. The encryption oracle accepts 12-byte chosen plaintext but forbids the literal `give me flag`; AD length is controllable (in 8-byte increments) and its tail is generated randomly. The decryption oracle accepts attacker-supplied nonce, ciphertext, AD and 16-bit tag. The server has 168 total requests. Neither 3DES itself nor GCM's normal 128-bit tag security applies to this construction.

See the [full solving notes](solve.md).

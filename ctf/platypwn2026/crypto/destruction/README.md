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


## Recovered original working solver

Original, live-validated Python solver recovered from the earlier conversation's files. It makes one large-AD encryption request, uses 32 selected GHASH variants to recover the 64-bit GHASH key with GF(2) elimination, and completes both flag conditions. The user confirmed that it worked unchanged on the live service in approximately 80/168 calls.

```bash
python3 scripts/destruction_full_solve.py <host> <port>
```

Code: [scripts/destruction_full_solve.py](scripts/destruction_full_solve.py). The file contains the algorithm and checks, not a hardcoded live service address.

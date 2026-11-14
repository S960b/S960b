# DEStruction: solving notes

[По-русски](solve.ru.md)

## 1. Task and source material

An improvised GCM construction based on 3DES with 64-bit blocks and only a two-byte authentication tag. The service has separate verification conditions for two flags. Source files: `server.py`, `encryption.py`, Dockerfile, compose; submission command records.

## 2. Analysis

`DES3_GCM` defines `H=DES3_K(0^64)`, a custom `GHASH(..., blen=64)` and `tag=(GHASH(H, AD, ciphertext) XOR E_K(Y0))[:2]`. The encryption oracle accepts 12-byte chosen plaintext but forbids the literal `give me flag`; AD length is controllable (in 8-byte increments) and its tail is generated randomly. The decryption oracle accepts attacker-supplied nonce, ciphertext, AD and 16-bit tag. The server has 168 total requests. Neither 3DES itself nor GCM's normal 128-bit tag security applies to this construction.

## 3. Solving procedure

1. Study `get_ctr()` and the nonstandard 64-bit GHASH, including the separate processing of AD and ciphertext bit lengths.
2. Query chosen plaintexts and use collisions/relations in the short-tag GHASH authentication to constrain the secret GHASH subkey `H`.
3. The solved session recovered the 64-bit H value (recorded independently as `fc553726437d45c2`) and then constructed authenticated modified inputs that pass the two command checks (`give me flag` and a plaintext containing H).
4. Submit the forged quadruples through the ciphertext submission option. The operator reports two accepted flags after about 80 of 168 permitted requests.

## 4. Evidence and result

All algorithmic details, tag truncation and both flag checks are present in the provided source. Previous live-session report records the recovered GHASH subkey and the two accepted flags. The actual forged payloads and solver implementation were not included in this archive.

Result: **2 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

The exact algebraic search procedure and forged nonce/AD/tag values are missing. We do not claim that the abbreviated description here is a plug-and-play exploit.

## 6. Fix and lesson

Do not build custom GCM variants over 64-bit block ciphers; use a vetted AEAD (AES-GCM, ChaCha20-Poly1305) with correctly sized tags and unique nonces.

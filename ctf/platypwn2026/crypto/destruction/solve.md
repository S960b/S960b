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

All algorithmic details, tag truncation and both flag checks are present in the provided source. Previous live-session report records the recovered GHASH subkey and the two accepted flags. The original script was recovered from the earlier work and is now published in `scripts/destruction_full_solve.py`; the raw terminal output of the live deployment is not in the transferred ZIP. The procedure in sections 7–8 documents the recovered solver rather than an invented reconstruction.

Result: **2 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

The original solver in `scripts/destruction_full_solve.py` includes the complete algebraic procedure and generates nonce, AD and tag values dynamically. Run it as documented in section 8. The separate live transcript is missing, but the script itself is published.

## 6. Fix and lesson

Do not build custom GCM variants over 64-bit block ciphers; use a vetted AEAD (AES-GCM, ChaCha20-Poly1305) with correctly sized tags and unique nonces.


## Recovered original working solver

Original, live-validated Python solver recovered from the earlier conversation's files. It makes one large-AD encryption request, uses 32 selected GHASH variants to recover the 64-bit GHASH key with GF(2) elimination, and completes both flag conditions. The user confirmed that it worked unchanged on the live service in approximately 80/168 calls.

```bash
python3 scripts/destruction_full_solve.py <host> <port>
```

Code: [scripts/destruction_full_solve.py](scripts/destruction_full_solve.py). The file contains the algorithm and checks, not a hardcoded live service address.


## 7. How the recovered original solver actually works

The provided `server.py` allows at most `MAX_ATTEMPTS=168` oracle calls, accepts a 12-byte plaintext for encryption, and rejects the literal command `b"give me flag"` from the encryption oracle. In `encryption.py`, `TAG_LENGTH=2`, the DES3 block is 64 bits, and `GHASH(..., blen=64)` works in `GF(2^64)` with reduction constant `0xD800000000000000`.

The tag computation is:

```text
H = DES3_K(0^64)
tag = first_2_bytes(GHASH_H(AD,C) XOR DES3_K(Y0))
```

To obtain `FLAG0` the service must decrypt a valid authenticated ciphertext containing `give me flag`. For `FLAG1` the decrypted text must contain the secret **eight-byte H** itself.

### 7.1. One ordinary encryption with large AD

The solver asks the encryption oracle to encrypt `PLAINTEXT=b"ABCDEFGHflag"` with `AD_REQUEST_LENGTH=1<<20` (the service returns four additional AD bytes because of its own length adjustment). This grants:

- a known 12-byte plaintext and therefore known CTR keystream at that nonce;
- the same four-byte suffix `flag` in both the chosen text and `give me flag`, so only the first eight ciphertext bytes need to change;
- a large number of 64-bit AD blocks for compensating GHASH changes.

If the original ciphertext is `C`, the desired `C'` is:

```python
delta = bytes(x ^ y for x,y in zip(b"ABCDEFGHflag", b"give me flag"))
C_prime = bytes(x ^ y for x,y in zip(C, delta))
```

CTR will decrypt `C'` to the desired command under the same nonce, but the changed ciphertext now requires a compatible authentication tag.

### 7.2. Thirty-two chosen GHASH variants, two bits each

The actual recovered solver embeds `VARIANTS_B85`, a zlib/base85 compressed table of **32** specifically constructed AD variants. Each variant contains coefficients for **15** 64-bit AD blocks and two 64-bit linear rows, `row_hi` and `row_lo`, describing its dependence on the unknown H.

In `make_forgery()`, the coefficients are XORed into 8-byte AD chunks at computed offsets. Each variant preserves the top **14 of 16 tag bits** while leaving only the bottom **two** unknown.

The script submits candidates with low-bit tag differences 0, 1 and 2. If all three fail, 3 is implied without consuming another query. Every variant therefore reveals two independent linear bits of H. After 32 variants, the solver has a 64-row GF(2) system:

```text
row_hi[i] · H = high_bit[i]
row_lo[i] · H = low_bit[i]      i = 0..31
```

These coefficient sets are tailored to this exact challenge's GHASH reduction polynomial. The original script contains their full compressed representation; the numbers are not guessed during the run.

### 7.3. Recovering the 64-bit GHASH key

`solve_binary_system(rows,values)` performs binary Gaussian elimination using 64-bit masks, checks for a full rank of 64 and verifies the recovered H against every original equation.

The live run recovered the full eight-byte H. Its numerical value belongs to one challenge instance; it must not be treated as a constant key for new deployments.

A successful forged submission already yields `FLAG0`, since its authenticated plaintext contains the required command.

### 7.4. Obtaining FLAG1 after H is known

A second forgery is unnecessary. The encrypt oracle accepts the 12-byte plaintext `H || b"zzzz"` because it is **not** `give me flag`. The solver requests a normal encryption of those bytes and submits the unmodified nonce, ciphertext, AD and tag to the decryption oracle. The plaintext includes H, triggering the second flag condition.

This is why the hard part is recovering H, while the final FLAG1 transaction is simple once H is available.

## 8. Oracle budget and live confirmation

At most three candidate tag values are tried for each of 32 variants, so the recovery needs at most 96 decryption queries plus a few encryption/final validation calls, within the 168-request cap. The owner reported a live success with approximately **80 requests out of 168**, both accepted flags, and **no changes to the script**.

```bash
python3 scripts/destruction_full_solve.py <host> <port>
```

The script prints the number of collected equations, the recovered H and both flag results. Its compressed coefficients and full oracle orchestration are now published, rather than paraphrased.

## 9. Security lesson

A 16-bit tag and a nonstandard 64-bit GHASH created a two-bit authentication oracle when chosen AD was allowed. The failure was not just 'DES is old', but the unreviewed combination of a custom mode, an undersized authenticator and attacker-controlled associated data.

## Repository evidence

Source code and redacted supporting artifacts: [`assets/server.py`](assets/server.py), [`assets/encryption.py`](assets/encryption.py), [`scripts/destruction_full_solve.py`](scripts/destruction_full_solve.py), [`scripts/destruction_solve.py`](scripts/destruction_solve.py).

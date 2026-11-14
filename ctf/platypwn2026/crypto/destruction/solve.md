# DEStruction: recovering the GHASH key from a 16-bit tag

[По-русски](solve.ru.md)

## 1. Goal, evidence and reproduction

The service implements a custom 3DES-based GCM variant with a **64-bit block cipher**, user-controlled associated data and a **two-byte authentication tag**. It allows 168 oracle requests. Two independent checks must be passed: authenticated decryption containing `give me flag`, and authenticated decryption containing the hidden 8-byte GHASH subkey `H`.

**Result:** both flags recovered and accepted in the live challenge, about 80/168 requests. The recovered original solver is in [`scripts/destruction_full_solve.py`](scripts/destruction_full_solve.py). Run `python3 scripts/destruction_full_solve.py HOST PORT`. Its 32 precomputed AD-variant sets, binary linear solver and validation logic are all preserved.

**Evidence:** original [`server.py`](assets/server.py) and [`encryption.py`](assets/encryption.py), plus the recovered full solver and [archived analysis](evidence/DEStruction_README_RU.txt). The terminal transcript and instance-specific nonce/AD/tag values were not included in the archive. The GHASH key printed during the successful session was instance-specific, not reusable across deployments.

## 2. How the recovered original solver actually works

The provided `server.py` allows at most `MAX_ATTEMPTS=168` oracle calls, accepts a 12-byte plaintext for encryption, and rejects the literal command `b"give me flag"` from the encryption oracle. In `encryption.py`, `TAG_LENGTH=2`, the DES3 block is 64 bits, and `GHASH(..., blen=64)` works in `GF(2^64)` with reduction constant `0xD800000000000000`.

The tag computation is:

```text
H = DES3_K(0^64)
tag = first_2_bytes(GHASH_H(AD,C) XOR DES3_K(Y0))
```

To obtain `FLAG0` the service must decrypt a valid authenticated ciphertext containing `give me flag`. For `FLAG1` the decrypted text must contain the secret **eight-byte H** itself.

### 2.1. One ordinary encryption with large AD

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

### 2.2. Thirty-two chosen GHASH variants, two bits each

The actual recovered solver embeds `VARIANTS_B85`, a zlib/base85 compressed table of **32** specifically constructed AD variants. Each variant contains coefficients for **15** 64-bit AD blocks and two 64-bit linear rows, `row_hi` and `row_lo`, describing its dependence on the unknown H.

In `make_forgery()`, the coefficients are XORed into 8-byte AD chunks at computed offsets. Each variant preserves the top **14 of 16 tag bits** while leaving only the bottom **two** unknown.

The script submits candidates with low-bit tag differences 0, 1 and 2. If all three fail, 3 is implied without consuming another query. Every variant therefore reveals two independent linear bits of H. After 32 variants, the solver has a 64-row GF(2) system:

```text
row_hi[i] · H = high_bit[i]
row_lo[i] · H = low_bit[i]      i = 0..31
```

These coefficient sets are tailored to this exact challenge's GHASH reduction polynomial. The original script contains their full compressed representation; the numbers are not guessed during the run.

### 2.3. Recovering the 64-bit GHASH key

`solve_binary_system(rows,values)` performs binary Gaussian elimination using 64-bit masks, checks for a full rank of 64 and verifies the recovered H against every original equation.

The live run recovered the full eight-byte H. Its numerical value belongs to one challenge instance; it must not be treated as a constant key for new deployments.

A successful forged submission already yields `FLAG0`, since its authenticated plaintext contains the required command.

### 2.4. Obtaining FLAG1 after H is known

A second forgery is unnecessary. The encrypt oracle accepts the 12-byte plaintext `H || b"zzzz"` because it is **not** `give me flag`. The solver requests a normal encryption of those bytes and submits the unmodified nonce, ciphertext, AD and tag to the decryption oracle. The plaintext includes H, triggering the second flag condition.

This is why the hard part is recovering H, while the final FLAG1 transaction is simple once H is available.

## 3. Oracle budget and live confirmation

At most three candidate tag values are tried for each of 32 variants, so the recovery needs at most 96 decryption queries plus a few encryption/final validation calls, within the 168-request cap. The owner reported a live success with approximately **80 requests out of 168**, both accepted flags, and **no changes to the script**.

```bash
python3 scripts/destruction_full_solve.py <host> <port>
```

The script prints the number of collected equations, the recovered H and both flag results. Its compressed coefficients and full oracle orchestration are now published, rather than paraphrased.

## 4. Security lesson

A 16-bit tag and a nonstandard 64-bit GHASH created a two-bit authentication oracle when chosen AD was allowed. The failure was not just 'DES is old', but the unreviewed combination of a custom mode, an undersized authenticator and attacker-controlled associated data.

## Repository evidence

Source code and redacted supporting artifacts: [`assets/server.py`](assets/server.py), [`assets/encryption.py`](assets/encryption.py), [`scripts/destruction_full_solve.py`](scripts/destruction_full_solve.py), [`scripts/destruction_solve.py`](scripts/destruction_solve.py).


## Local challenge environment

The original [`Dockerfile`](assets/Dockerfile) and [`compose.yaml`](assets/compose.yaml) now live alongside the service sources in [`assets/`](assets/). Compose defines **dummy flags**, not competition flag values. With Docker Compose installed:

```bash
cd ctf/platypwn2026/crypto/destruction/assets
docker compose up --build
# From another terminal in the same directory:
python3 ../scripts/destruction_full_solve.py 127.0.0.1 2004
```

This recreates the protocol with freshly initialized server state. New keys and random values will not match the historical live deployment. Docker build execution was not independently tested during this documentation audit.

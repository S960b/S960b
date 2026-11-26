# Cold Storage — AES-GCM nonce reuse through Temporal leap-second normalization

[Русская версия](solve.ru.md)

## 1. Source and application

The organizer's Node.js/Express project contains `src/server.js`, `src/archive.js` and `src/record-crypto.js`. Each instance generates a fresh **random 16-byte AES key** and 32 public archived encrypted records. The archive records are deterministic across instances because `entrypoint.sh` runs Node with `--random-seed=1337`; the AES key is **not** deterministic.

Endpoints:

- `GET /archive`: 32 entries with `recorded`, `sample`, `classification`, `ciphertext` and `tag`; form-URL-encoded rather than JSON.
- `POST /api/import`: a **single** permitted import, forcing `classification=open` and sample matching `^[a-z0-9-]{2,21}$`.
- `POST /api/open`: validates timestamp and ciphertext/tag formats, decrypts, and returns the flag only when `classification === 'restricted'` and `sample === 'Ice-VII'`.
- `GET /api/context`: publishes these target fields.

The record plaintext is exactly **32 bytes**:

```javascript
Buffer.from(`${classification.padEnd(10)}|${sample.padEnd(21)}`)
```

Encryption is AES-128-GCM with no AAD. The 12-byte nonce is derived using:

```javascript
sha256(Temporal.Instant.from(recorded).epochNanoseconds.toString()).subarray(0,12)
```

## 2. The time parser and string comparison disagree

The archive contains a predictable entry with `recorded=1972-12-31T23:59:59Z` and `sample=ice-e1512db7`. The attacker imports `recorded=1972-12-31T23:59:60Z` with `sample=ice-eaaa`.

The duplicate check is:

```javascript
archive.some(record => record.recorded === recorded)
```

These strings differ, so the import is allowed. But `Temporal.Instant.from` normalizes leap second `:60` to `:59`: both produce the **same epochNanoseconds and nonce**. There is no carry into the next minute. A negative probe using `1988-03-04T08:30:60Z` against `08:31:00Z` returned HTTP 403, while the `:59` equivalence yielded the successful forgery.

`/api/import` is limited to one **successful** use per instance. It does not insert the returned record into the public archive. The predictable archive timestamp is available in fresh instances; a malformed import returns HTTP 400 before consuming the one-time ability.

## 3. Recover the GCM authentication key

With the same key and nonce, the GCM counter-mode keystream is identical, so the two known 32-byte plaintexts yield related ciphertext blocks.

Choose the import sample beginning with `ice-e`, the same five-character prefix as the archived sample. Then the first **16-byte** plaintext blocks coincide, hence `C1[0:16] == C2[0:16]` and `ΔC_first = 0`.

GCM's authentication is:

```text
Tag = E_K(J0) XOR GHASH_H(C)
H   = AES_K(0^128)
GHASH_H(C1,C2) = ((C1·H XOR C2)·H XOR L)·H
L = 256   (ciphertext length in bits; no AAD)
```

Subtracting two tags over GF(2¹²⁸), all constant terms and the identical first blocks cancel:

```text
ΔT = ΔC_second · H²
H² = ΔT / ΔC_second
H = (ΔT / ΔC_second)^(2^127)
```

Field division uses exponentiation to `2^128-2` for inversion, and the square root uses `2^127` successive squarings in characteristic 2. The multiplication polynomial is `x^128+x^7+x^2+x+1`.

From the successful run:

```text
H = 8ac7c6018c46310301929f354247b6f0
```

This was **checked** against both ciphertext/tag pairs: `GHASH_H(C1) XOR T1 == GHASH_H(C2) XOR T2`. The result depends on that particular instance's random key; it is not a general hardcoded secret.

## 4. Forge the restricted record

The importer returns the encryption of the chosen known plaintext `P1`. Since `C1 = P1 XOR KS`, recover the 32-byte keystream `KS = P1 XOR C1`.

The target encoded plaintext is exactly:

```python
P_target = ("restricted" + "|" + "Ice-VII".ljust(21)).encode()
assert len(P_target) == 32
```

Forge:

```text
C_target = P_target XOR KS
E        = Tag1 XOR GHASH_H(C1)
Tag_target = E XOR GHASH_H(C_target)
```

Send `C_target` and `Tag_target` to `/api/open` **with the original archive timestamp** (`1972-12-31T23:59:59Z`). The reused nonce makes the forged GCM tag valid, the plaintext decodes to `restricted|Ice-VII`, and the application returns its `FLAG`.

The preserved remote transcript shows:

```text
records 32
target record 1972-12-31T23:59:59Z ice-e1512db7
alias 1972-12-31T23:59:60Z
import 200 ...
H 8ac7c6018c46310301929f354247b6f0
H verified
FORGED OPEN: 200 message=Record+opened.&classification=restricted&sample=Ice-VII&...&flag=gctf%7B...%7D
```

The URL-encoded flag was successfully returned and subsequently accepted by the platform (participant-confirmed). Its value is hidden in this public repository according to the project's convention.

## 5. Why other investigations failed

The research archive records substantial negative tests before inspecting the source:

- Tens of millions of candidate public-key/nonce derivations (hashes of timestamps, sample names, other metadata, and combinations): none worked. The key is `randomBytes(16)` per instance.
- 496 pairwise ciphertext comparisons and 4,960 triple checks for repeated nonces **among archive entries**: no collisions. The exploitable collision is created using **one import plus one archive record**.
- Route enumeration, headers and HTTP method manipulations failed to expose the restricted sample.
- Timing measurements between successful/failed `/api/open` operations were not useful.
- Guessing that leap second `:60` rolled over to the following `:00` proved false; Temporal normalizes it to the preceding `:59`.
- A malformed alias ending in `:560Z` was rejected by the timestamp regex (HTTP 400) and did not spend the one-time import.

## 6. Files and reproducibility

The participant-provided `glacierctf_cold-storage_materials.zip` and `glacierctf_cold-storage_materials(1).zip` are identical. Both contain the organizer's original tarball, extracted Node.js sources, the actual successful `scripts/solve.py`, helper scripts `session.py` and `exploit.py`, intermediate experiments, 10 archived instance datasets and `evidence/03_successful_run.log`.

The [public standalone solver](scripts/solve.py) removes the author's hardcoded `/tmp/gigatool/cold` import path and supports explicitly passing the instance URL or spawning a new one. The [offline test](scripts/test_offline.py) validates the GHASH derivation using ciphertexts from the historical run and archive, without network use.

The remote instance expired; no new session was opened while producing this write-up.

## 7. Defense

A GCM nonce must be unique for every encryption under a given key. Nonces derived from untrusted, noncanonical strings are risky when uniqueness is enforced using a different representation of those strings. Normalize first and check uniqueness on the **normalized value**, or generate a unique independent nonce and store it with the ciphertext. Avoid exposing a chosen-plaintext encryption oracle under a key shared with protected records.

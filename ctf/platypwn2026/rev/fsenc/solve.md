# FSenc: investigating an Argon2id-derived file header

[По-русски](solve.ru.md)

## 0. What survived and what did not

The recorded result marked FSenc solved and the result was accepted, but the export contains **no original encryptor, no `flag.bin`, no `a.enc`, no recovered password, and no successful decryption output**. It does contain two substantial fragments of the actual terminal work, `evidence/solve_1.txt` and `solve_2.txt`. Those fragments are the basis for this document.

The recorded work pursued two related hypotheses: the 44 bytes following a 12-byte file prefix were either immediately usable as a 32-byte ChaCha20-Poly1305 key and 12-byte nonce, or they were a password-derived 44-byte value computed with Argon2id. These are **different interpretations** of the header. The source snippets show tests for both; they do not include their outputs, so the successful hypothesis cannot be identified with confidence.

This matters: merely observing `key = header[:32]` in a testing script does **not** prove the encryptor stored an unprotected key. In particular, the brute-force script explicitly treats those same bytes as an Argon2id output target.

## 1. File layout inferred by the operator

The original script reads:

```python
f = open('flag.bin', 'rb').read()
hdr = f[12:56]
body = f[56:]
key, nonce = hdr[:32], hdr[32:44]
```

The script therefore assumes:

```text
0               12              44               end
+---------------+---------------+----------------+
| prefix/magic  | 44-byte field | encrypted body |
+---------------+---------------+----------------+
                | 32-byte key | 12-byte nonce |
```

These offsets are **observed operator assumptions**, not a verified file specification: the encryptor source and the file itself were not saved.

The first test tries authenticated decryption:

```python
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
plaintext = ChaCha20Poly1305(hdr[:32]).decrypt(hdr[32:44], body, None)
```

AEAD authentication is useful here. A valid tag would substantiate that the split matches a real key and nonce; an exception would falsify that straightforward interpretation. The archived script logs either `PLAINTEXT:` or `fail:`, but the output line is absent, so we **cannot say which occurred**.

## 2. The Argon2id hypothesis

The same log then writes a `crack.py` to test password candidates against this 44-byte value:

```python
from argon2.low_level import hash_secret_raw, Type
magic = bytes.fromhex("4ca36c80fff763eb2be4224f")
SALT = magic + magic[:4]
TARGET = open("flag.bin", "rb").read()[12:56]

def check(password):
    h = hash_secret_raw(
        password, SALT, time_cost=2, memory_cost=19456,
        parallelism=1, hash_len=44, type=Type.ID
    )
    return h == TARGET
```

This defines a 16-byte salt made from twelve magic bytes plus the first four bytes repeated. Argon2id returns exactly 44 bytes, matching the putative key-plus-nonce field length. If the file format exposes that result, each dictionary guess can be checked **offline** without talking to an oracle.

The command saved in the archive loaded up to 30,000 words from `rockyou.txt`, ran eight worker processes with `chunksize=64`, and stopped after 115 seconds:

```sh
timeout 115 python3 -u crack.py /usr/share/wordlists/rockyou.txt 30000
```

The Argon2id parameters are expensive enough that a 115-second timeout does not imply all 30,000 guesses were tested. The archived record shows the invocation, not a successful `FOUND:` line. Claiming a cracked password from this would be fabrication.

## 3. Testing known-password consistency

A third fragment evaluates the same KDF on the literal test password `testpass` and attempts to decrypt a test file `a.enc` with the derived first 32 bytes (key) and final 12 bytes (nonce):

```python
h = hash_secret_raw(b"testpass", salt, 2, 19456, 1, 44, Type.ID)
key, nonce = h[:32], h[32:]
data = open("a.enc", "rb").read()
plaintext = ChaCha20Poly1305(key).decrypt(nonce, data[56:], None)
```

Why this step matters: if `a.enc` is an intentionally generated known-password control file, it can verify whether the recovered Argon2id value really is used as key and nonce. But the transcript contains neither the test file nor the printed plaintext, so the positive outcome remains unknown.

## 4. Reproducing what is actually testable

The accompanying [scripts/inspect_fsenc.py](scripts/inspect_fsenc.py) performs both **non-destructive** tests on supplied local files:

```sh
python3 scripts/inspect_fsenc.py flag.bin --direct
python3 scripts/inspect_fsenc.py flag.bin --password testpass
python3 scripts/inspect_fsenc.py flag.bin --wordlist /usr/share/wordlists/rockyou.txt --limit 30000
```

The script never contains the accepted flag or a claimed recovered password. It only prints actual outcomes from whatever file is passed. The standalone script **does not recover the missing files**; they must be supplied from the original challenge archive or an independently recovered working directory.

## 5. Evidence table

| Claim | Status |
|---|---|
| Operator sliced bytes 12–55 as a 44-byte header | Directly recorded |
| Key candidate occupies 32 bytes, nonce candidate 12 | Directly recorded |
| Argon2id parameters: t=2, m=19456, p=1, output 44 | Directly recorded |
| 16-byte salt from 12-byte magic plus its first four bytes | Directly recorded |
| Test dictionary run with 8 workers, limit 30,000 and 115-second timeout | Directly recorded |
| Simple direct header decryption succeeds | **No output preserved** |
| Dictionary attack recovered a specific password | **No output preserved** |
| Challenge flag accepted | Final result record, `PP{...}` (masked) |

## 6. What would complete the proof

Recovering `flag.bin`, the encryptor implementation or the original `crack.py` run output would settle which hypothesis worked. Without them, no exact decryption command can be honestly claimed as the final successful exploit.

The general engineering lesson depends on which hypothesis is true. If the field is a raw key, storing it alongside ciphertext is catastrophic. If it is an exposed Argon2id digest of a low-entropy password, offline guessing is possible; increasing KDF cost slows guessing but does not replace a high-entropy secret. Either way, file headers must be authenticated and key derivation reviewed.

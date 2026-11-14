# FSenc: solving notes

[По-русски](solve.ru.md)

## 1. Task and source material

File-encryption reverse engineering. The decisive observation was the format of the encrypted file header. Source files: Preserved decryption/Argon2 test commands; original encrypted data files not included.

## 2. Analysis

The saved command interprets bytes `12:56` of `flag.bin` as a 44-byte field: first 32 bytes as a ChaCha20-Poly1305 key and the next 12 bytes as its nonce. Ciphertext begins at offset 56; the recorded test calls `ChaCha20Poly1305(key).decrypt(nonce, body, None)`. Another experiment computes `argon2.low_level.hash_secret_raw` with Argon2id (`time_cost=2`, `memory_cost=19456`, `parallelism=1`, output 44 bytes) and a deterministic 16-byte salt. This explains the attempted password path, but does not establish that the password was recovered.

## 3. Solving procedure

1. Parse the file as `magic (12 bytes) | header (44 bytes) | encrypted body`.
2. Split the 44-byte header into `key=header[:32]` and `nonce=header[32:44]`.
3. Try ChaCha20-Poly1305 authenticated decryption of `data[56:]` with no associated data.
4. The older password-cracking experiment used a wordlist against Argon2id; do not claim that this was necessary for the successful result unless the missing run output is recovered.

## 4. Evidence and result

The archive includes exact Python snippets for both the header-based decrypt attempt and the Argon2id test. The agent marks the challenge solved and the flag accepted, but the captured command file does not include the plaintext output.

Result: **1 accepted flag(s)**, masked `PP{...}`.

## 5. Reproduction limits

No `flag.bin`, encryptor executable, or successful decrypted output was attached. A complete standalone reproduction cannot be verified from this archive.

## 6. Fix and lesson

Never serialize a secret key next to the encrypted data as an unprotected header. A nonce may be public; a key must be derived from a protected secret and the file format must authenticate metadata.

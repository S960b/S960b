# FSenc

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** Platypwn 2026
- **Category:** rev
- **Difficulty:** 2
- **Materials:** Preserved decryption/Argon2 test commands; original encrypted data files not included
- **Flags:** 1 accepted, exact strings masked (`PP{...}`)

File-encryption reverse engineering. The decisive observation was the format of the encrypted file header.

**Core issue:** The saved command interprets bytes `12:56` of `flag.bin` as a 44-byte field: first 32 bytes as a ChaCha20-Poly1305 key and the next 12 bytes as its nonce. Ciphertext begins at offset 56; the recorded test calls `ChaCha20Poly1305(key).decrypt(nonce, body, None)`. Another experiment computes `argon2.low_level.hash_secret_raw` with Argon2id (`time_cost=2`, `memory_cost=19456`, `parallelism=1`, output 44 bytes) and a deterministic 16-byte salt. This explains the attempted password path, but does not establish that the password was recovered.

See the [full solving notes](solve.md).

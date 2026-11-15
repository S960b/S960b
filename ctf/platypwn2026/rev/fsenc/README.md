# FSenc

[Русская версия](README.ru.md) | [Full walkthrough](solve.md)

**Category:** Reverse engineering · **Difficulty:** 2 · **Result:** platform-accepted flag

The file header disclosed both the **32-byte ChaCha20-Poly1305 key** and **12-byte nonce**. The encrypted body could therefore be authenticated and decrypted directly, without recovering the Argon2id password.

Flag: `PP{f1l3_3ncrypt10n_1s_h4rd::A1bk0dIsUfPL}`.

[Successful decryption](solve.md) · [Execution checkpoint](evidence/decryption-checkpoint.md)

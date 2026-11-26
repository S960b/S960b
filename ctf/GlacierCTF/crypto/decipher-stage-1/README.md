# Decipher Stage 1

[Русская версия](README.ru.md) · [Technical write-up](solve.md)

**GlacierCTF 2026 · Decipher Stage 1 · crypto · 50 pts**

**Status: Solved — Flag accepted by platform (participant confirmation).** The accepted flag is redacted in public text as `gctf{...}`.

## The attack

The provided DeCipher challenge archives contain `decipher.py`, `output_stage1.npz`, 16 known plaintexts and ciphertexts, 10-round pairwise XOR proof traces, a wrapped AES key and the encrypted flag.

Its custom substitution box is **almost affine**. Computing the set of translations `V = {v : S[x^v]^S[x] = S[v]^S[0] for every x}` yields exactly **64 elements**, a 6-dimensional linear subspace with basis `[0x80, 0x22, 0x10, 0x08, 0x04, 0x01]`. Every observed S-box difference restricts its input to a 64-element coset; it does not uniquely reveal that input.

The original solver uses 15 pairwise execution proofs to identify the coset for each byte of the reference run in each round. It represents all AES-like state transformations as **GF(2) linear expressions**, with **128 key bits plus 40 auxiliary key-schedule nonlinearity bits** (168 variables). After incorporating the observed coset constraints and known final ciphertext, it uses binary Gaussian elimination, recovers the 16-byte base key, and verifies all 16 ciphertexts by performing genuine encryption.

The recorded recovered base key is `fc417d0d233a97a57521f12792672e88`. The solver then decrypts the wrapped flag key with DeCipher, producing `9f291cd4aced45919deae8677ef88f0f`, and decrypts the flag ciphertext using AES-GCM with a 12-byte all-zero nonce. The original session verified the AES-GCM tag.

**Reproduction:** extract the original `decipher-1` directory (or its tar.gz), install `numpy` and `cryptography`, then run `python3 scripts/decipher_stage1_solver.py PATH_TO_CHALLENGE`. It uses `archive.extractall(..., filter='data')` and imports the organizer's `decipher.py` at runtime. No network is needed.

The exact accepted flag is preserved in the previous conversation; this public write-up masks it as `gctf{...}`.


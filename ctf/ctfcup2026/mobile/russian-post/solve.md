# Russian Post: Trust Level

[По-русски](solve.ru.md)

## 0. What it is

An Android app for a parcel service. The flag arrives sealed from the backend and is decrypted on the client, so the task is to reproduce the client key flow, not to break an encryption primitive.

Two things are in the archive: the APK and the backend protocol. The backend lives on the challenge host and answers `/api/config`, `/api/parcel` and `/api/key`.

## 1. Reading the client

The APK contains Java code and a Go native library. The Java part shows three calls in order: fetch a challenge and session, post the device profile to get a sealed container, post the same data plus the receipt to get the key. The native part, `mobilelib.VerifySealedParcel`, does the decryption.

The interesting detail is a deliberate trap in the Java layer. `MainActivity.a()` builds the `/api/key` request with empty `challenge`, `session` and `receipt`. That request always fails. The real builder, `keyRequestFromPreferences()`, exists but is never called. So a solver that follows the visible call path gets a rejection, and the task is to notice that the app is sabotaging itself.

Why this step: the encryption is custom but simple; the difficulty is in reconstructing which request the server actually accepts.

## 2. The container format

`VerifySealedParcel` decodes three base64 strings: an IV, a key and the ciphertext. It checks that the IV and key are 16 bytes and that the ciphertext length is a multiple of 16.

Decryption is blockwise, not a standard mode:

```text
plain_block[i] = cipher_block[i] XOR cipher_block[i-1] XOR key[i]
```

with `cipher_block[-1] = IV`. This is a chained XOR, so a single wrong block corrupts two blocks of plaintext. PKCS#7 padding is validated at the end, which gives an oracle for a wrong key: the padding check fails before any text is shown.

Why this step: knowing the exact chaining rule is what allows the decryption to be written from the recovered key, without running the APK.

## 3. The request that works

The accepted flow:

1. `GET /api/config` returns `challenge`, `session` and `expires_at`.
2. `POST /api/parcel` with package name, version, `challenge`, `expires_at`, `session`, `rooted`, `fingerprint`, `model`, `android_id` returns the container, the IV and a `receipt`.
3. `POST /api/key` with the same fields plus `receipt` returns the 16-byte key.

One deployment detail cost an iteration: the server returns the sealed container in a field named `parcel`, while the app reads `sealed_parcel`. A solver that only looks at the app's field name finds nothing and reports a schema mismatch. Accepting both names fixes it.

Why this step: the key is only issued for a matching receipt, so the two posts are bound together; skipping the receipt yields an error, not a key.

## 4. Decryption

With the key, the IV and the ciphertext, the routine above produces the plaintext, and the flag is inside it. `scripts/solve_sealed_prefs.py` walks the whole chain and prints the decrypted text. The flag is masked in this repository as `ctfcup{...}`.

```bash
python3 scripts/solve_sealed_prefs.py --base https://<host>
```

The script prints every backend response, which is deliberate: the field naming differs between deployments, and seeing the JSON is faster than guessing.

## 5. Dead ends

- Static `strings` on the APK yields no flag: the container arrives at runtime.
- Replaying the flawed request from `MainActivity.a()` always fails; it is a decoy, not a bug to exploit.
- Assuming AES: the native routine is a chained XOR with a 16-byte key, so trying `openssl` or PyCryptodome modes produces garbage that fails padding.

## 6. Lessons

- In mobile tasks, separate the crypto from the request plumbing; the plumbing is usually where the challenge lives.
- Trust the server's field names over the client's: a mismatch here is a hint about the deployment, not a broken solver.
- A decoy code path is a legitimate part of the puzzle; check which functions are actually called.

One paragraph: the app fetches a challenge, exchanges it plus a device profile for a sealed container and a receipt, then trades the receipt for a 16-byte key, and decrypts the container with a chained XOR over 16-byte blocks; the visible Java path sends an empty receipt on purpose, so the real request has to be rebuilt from the unused builder, and the server names the container field differently from the client.

# Russian Post: Trust Level

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 337
- **Category:** mobile
- **Task:** https://game.ctfcup.online/tasks/mobile-russian-post
- **Attachment:** `sealed-prefs.apk`
- **Flag:** `ctfcup{...}` (masked)

An Android parcel app that receives a sealed flag from its backend. The client exchanges `GET /api/config` for `challenge` and `session`, posts the device profile to `/api/parcel` to get a container, an IV and a receipt, then posts the same data plus the receipt to `/api/key` to get a 16-byte key.

The visible Java path is a decoy: `MainActivity.a()` sends an empty receipt on purpose, and the working builder is never called. The native routine decrypts with a chained XOR, `plain[i] = cipher[i] XOR cipher[i-1] XOR key[i]`, then validates PKCS#7. One deployment detail matters, the server calls the container field `parcel` while the app reads `sealed_parcel`.

`scripts/solve_sealed_prefs.py --base https://<host>` walks the chain and prints the plaintext.

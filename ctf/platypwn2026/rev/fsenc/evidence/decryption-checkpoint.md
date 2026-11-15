# FSenc: preserved successful decryption (13 November 2026, 23:05)

The command loaded `flag.bin`, set `key=header[:32]` and `nonce=header[32:44]`, and called `ChaCha20Poly1305(key).decrypt(nonce, body, None)`.

```text
key   aa1ac2b4ea02d933d65a9c20d8a116f00e3d1094d03cd9d19cb75a9da4b399dd
nonce 8d3b427242a42d5c0df5e60f
PLAINTEXT: b'PP{f1l3_3ncrypt10n_1s_h4rd::A1bk0dIsUfPL}\n'
```

The recovered flag was accepted by the competition platform. This is the successful outcome of the original session rather than an inferred password attack.

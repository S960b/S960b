# Misdirection: Part 2 (crypto)

A WebAuthn and ECDSA challenge. Given: the sources of a service with a bot (`bot.js`,
`authenticator.js`, `auth-flow.js`, `config.js`), a Rust server side (`admin.rs`) with a `/report`
endpoint, and a 64-byte ciphertext of the hint.

Where the bug is:

- The bot signs WebAuthn logins with a nonce that is not random: `k = SHA256(rpId)`, where `rpId` is
  the login domain. The domain is public, so the nonce is known in advance.
- The `forgeAuthentication` function is exposed into the page through `page.exposeFunction` and
  takes `rpId` and `challenge` from the page, so the attacker page chooses what gets signed.
- The `/report` handler checks only the string prefix with `starts_with`, so a link like
  `https://frontend@attacker/auth` passes the check, and after the prefix replacement it points at
  the attacker host.

What came out of it:

- Two signatures with different `rpId` values gave the same private key by the formula
  `d = (s*k - z) * r^-1 mod n`.
- The key was verified against the public key from `init.sql`: the `x` and `y` coordinates matched.
- The hint was decrypted with AES-256-ECB, the key being SHA-256 of the lowercase hex form of `d`.
- The padding is non-standard: 28 bytes of value 28 were added, ordinary PKCS7 on a 16-byte block
  does not allow that, so it is removed by hand.

Files:

- `scripts/solve_misdir.py` - collects signatures and recovers the key, runs in three steps
- `scripts/verify_offline.py` - offline check: public key, formula, decryption
- `scripts/selftest.py` - self-test of the formula on synthetic signatures
- `assets/authenticator.js` - signing with the deterministic nonce
- `assets/bot.js`, `assets/auth-flow.js`, `assets/config.js` - the bot and its settings
- `assets/admin.rs` - the `/report` handler with the prefix check
- `assets/hint_ciphertext.hex` - the hint ciphertext

The full solving path is in `solve.md`.

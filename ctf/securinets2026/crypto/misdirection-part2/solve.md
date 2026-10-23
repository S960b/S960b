# Misdirection: Part 2 (crypto): the path to the solution

## 0. The whole plan

Why this section: so the reader sees the chain down to the details and understands why every next
step is needed.

Given: the sources of a service with a bot (`bot.js`, `authenticator.js`, `auth-flow.js`,
`config.js`, `admin.rs`), a public `/report` endpoint that makes the bot open a link, and a 64-byte
ciphertext of the hint. The site with WebAuthn registration and login is closed, but the bot sources
are open.

Two notes on names, to avoid confusion. The portal lists the task as `Misdirection: Part 2` in the
Cryptography category, while the source archive is called `misdir.zip` and some materials refer to
the task simply as Misdirection. The hint ciphertext came with the task statement: it is not inside
`misdir.zip`, I checked by searching all sources.

Goal: get the flag stored in the encrypted hint.

The plan came out like this:

1. Understand how the bot signs data and what in that signature can be computed in advance.
2. Make the bot sign our page, bypassing the link check in `/report`.
3. Recover the admin private key from a signature.
4. Verify the key against the public key from the database.
5. Decrypt the hint with that key and get the flag.

## 1. What is given

Why this step: to pin down the materials and to know where to look for the mistake.

The bundle holds the bot sources. `bot.js` starts a headless browser, opens the given link, registers
a random user and logs in. `authenticator.js` contains a hand-made WebAuthn implementation:
registration and login. `admin.rs` is the server side in Rust, and the `/report` handler that starts
the bot lives there.

The service also has a purely business path to the flag: the `/api/flag` route returns it to a user
whose balance is at least five million, while topping up `/api/admin/add_funds` is available only to
an administrator. We did not use that path, the flag came from the hint, but it is worth mentioning:
the service had two different ways to reach the flag, and the admin key matters for both.

On top of that the task provided the hint ciphertext. It is in `assets/hint_ciphertext.hex`, 64
bytes, that is exactly four AES blocks.

## 2. How WebAuthn login works

Why this step: without understanding the signature there is no way to see what is broken.

WebAuthn is a standard for passwordless login. The client has a key pair, the private key stays with
it and the public key is known to the server. At login the server sends a random string, the
challenge, the client builds a message from it and from the authentication data, signs that message,
and the server verifies the signature with the public key.

The signature here is ECDSA on the P-256 curve. In ECDSA a signature is a pair of numbers `r` and
`s`, computed like this: a random number `k`, called the nonce, is chosen, then the point `k*G` is
computed and `r` is taken from it, and `s` is computed as `s = k^-1 * (z + r*d) mod n`, where `z` is
the message hash, `d` the private key and `n` the curve order. The server, knowing `r`, `s`, `z` and
the public key, checks that the signature agrees.

The key property: if `k` is known, the private key is computed from a single signature. The formula
comes from a simple rearrangement: `d = (s*k - z) * r^-1 mod n`. That is why in ECDSA the nonce is
called one-time: it must not be repeated and must not be predictable.

## 3. Where the bug is

Why this step: to find what makes the nonce computable.

In `assets/authenticator.js` the signature is assembled by hand, and the nonce is not random:

```js
const rpIdHash = sha256(rpId);
...
const signature = keyPair.sign(msgHash, { k: () => ec.keyFromPrivate(rpIdHash, 'hex').getPrivate() });
```

So `k` equals SHA-256 of `rpId`, and `rpId` is the domain the login happens on. The domain is not a
secret, and its hash takes microseconds. So `k` is known in advance.

The second half of the bug is how the bot hands out signatures. `bot.js` exposes the function into
the page:

```js
await page.exposeFunction('forgeAuthentication', forgeAuthentication);
```

Such a function becomes available to any JavaScript on the opened page. And its first argument is
`rpId`, so the page itself chooses which domain to sign. On top of that `forgeAuthentication` signs
an arbitrary challenge, which also comes from the page.

In total: the attacker page can ask the bot to sign two different `rpId` values, and for each the
nonce is known.

## 4. Making the bot open our page

Why this step: a signature can only be obtained if the bot visits our site.

The `/report` handler in `admin.rs` takes a link and checks it like this:

```rust
if !payload.url.starts_with(&frontend_url) {
    return reply("error", format!("URL must start with {frontend_url}"));
}
```

Then the link is rewritten by replacing the prefix with the bot address:

```rust
let bot_url = payload.url.replacen(&frontend_url, &var("BOT_FRONTEND_URL"), 1);
```

The check looks only at the beginning of the string, while the link itself may contain the `@`
character, which in a URL separates user information from the host. So a string works where the real
frontend address comes first, then `@`, then our host. The check sees the familiar prefix and lets
it through, and after the prefix replacement the link has the frontend as user information and our
host as the host:

```text
https://frontend.example@attacker.example/auth
```

The host here is `attacker.example`, so the bot opens our site. To give the bot a real HTTPS address
I started a local server and exposed it through `cloudflared`:

```bash
python3 scripts/solve_misdir.py --listen --port 8877
./cloudflared tunnel --url http://127.0.0.1:8877
python3 scripts/solve_misdir.py --trigger --base 'https://<host>' --tunnel 'https://<tunnel>.trycloudflare.com'
```

Before that the solver sends a deliberately wrong link to `/report`, so the server names the
expected prefix in its error message, and takes `FRONTEND_URL` from that answer. It does not have to
be invented, the service hands it over.

The page the bot opens calls `forgeAuthentication` in its browser twice: with `rpId` `example.com`
and `example.org`. Both signatures, together with `authenticatorData` and `clientDataJSON`, go to our
server, which parses them.

## 5. Recovering the key

Why this step: to get the admin private key out of two signatures.

The server parses `authenticatorData`, checks that the first 32 bytes are SHA-256 of `rpId`, takes
`r` and `s` from the signature, computes `z` as SHA-256 of `authenticatorData` together with the
hash of `clientDataJSON`, and applies the formula:

```python
k = int.from_bytes(hashlib.sha256(rp.encode()).digest(), 'big') % N
d = ((s*k - z) * pow(r, -1, N)) % N
```

Why two different `rpId`: with one and the same `k` and one message the signature would be the same
and there would be nothing to check. Two different domains give two independent equations, and if
the formula is right both give the same `d`. That match is the check.

The second control is stronger: from the recovered `d` the public key can be derived and compared
with the one stored by the task. In the `init.sql` migration the admin passkey has the coordinates
`x = ax-gywE6mENnh-AHn4wRoEgrmC772tSG2jCRCKJQoZc` and
`y = 8YKWhWAp0RnCeXjFWXWXvXYhlmz9WIEO3FwHWYvOD2M`. My `scripts/verify_offline.py` computes the
point from the recovered `d` and compares: both coordinates match.

## 6. Decrypting the hint

Why this step: to actually get the flag.

The hint is 64 bytes, that is exactly four AES blocks, so the mode has no IV, that is ECB. The key
is not `d` itself but SHA-256 of its hexadecimal notation in lowercase, without a prefix:

```python
key = hashlib.sha256(f'{d:064x}'.encode()).digest()
```

That detail had to be found, and it also explains a message from the bundled solver. There the key
was taken as `d.to_bytes(32, 'big')` and the padding was removed as standard PKCS7, which is why the
script printed `PKCS7 padding invalid`: with the `d` bytes themselves, with reversed byte order and
with ordinary PKCS7 the decryption does not come together.

The padding is non-standard too. After decryption the end holds 28 bytes of value `0x1c`, that is
28. For AES the block is 16 bytes, and ordinary PKCS7 cannot add more than 16 bytes, so automatic
unpadding fails. The rule here is simpler: the last byte tells how many bytes were added, and all of
them equal that number. Remove them by hand and the string remains:

```
Securinets{sm0l...AMS}
```

I show the beginning and the end of the string on purpose, so it is visible that the flag was really
obtained.

The chain can be checked offline with one command:

```bash
python3 scripts/verify_offline.py
public x matches init.sql: True
public y matches init.sql: True
scalar recovered for example.com : True
scalar recovered for example.org : True
padding bytes: 28
flag: Securinets{sm0l...AMS}
```

## 7. What did not work and why

- A key made of the `d` bytes. The first attempt, `d.to_bytes(32, 'big')`, gives garbage: the task
  authors derived the key as SHA-256 of the hex string rather than from the bytes themselves.
- Ordinary PKCS7 unpadding. The 28 padding bytes are larger than the AES block, so the standard
  function refuses, and that is not a script bug but a different padding scheme.
- One `rpId` instead of two. With a single domain there is no way to check that the recovered key is
  stable, and an error in the formula would go unnoticed.
- Trying to do without a tunnel. The bot runs on the task server and will not reach localhost, so a
  public HTTPS address is required.

## 8. In one paragraph

The bot signed WebAuthn logins with a nonce equal to SHA-256 of the domain, and exposed the signing
function into the page together with the `rpId` argument. Through `/report` with a prefix-check
bypass it was possible to make the bot open our page, which asked it to sign two different domains.
From each signature the private key was recovered as `d = (s*k - z) * r^-1 mod n`, both values
matched, and the public key derived from it matched the key from the database. The hint decrypted
with AES-256-ECB using SHA-256 of the hex form of `d`, and after removing the non-standard padding
the flag appeared.

## 9. Conclusions

- In ECDSA the nonce must not be derived from public data: if `k` is known, the key is recovered from
  a single signature.
- Not only the formula is dangerous but also exposing the signing function to a page: it lets the
  page choose both the domain and the challenge.
- A string prefix check is no substitute for URL parsing: the `@` character changes the host even
  though the string starts with the expected address.
- A recovered key must be verified independently, against the public key from the database, not only
  by two signatures agreeing.
- The encryption scheme and the padding have to be confirmed by experiment: the key and the mode are
  found, not taken from a general rule.

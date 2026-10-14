#!/usr/bin/env python3
"""Publish the queued LinkedIn post via the official Posts API (one attempt).

Usage:
  python3 publish_queued.py [path/to/post.md] [--dry-run]

LinkedIn Posts API 'commentary' uses the 'little' text format: reserved
characters must be backslash-escaped to display as literal text. '#' is NOT
escaped so hashtags (#Cybersecurity ...) keep working. Newlines are preserved.
"""
import json, re, sys, urllib.request, urllib.error, pathlib

HOME = pathlib.Path.home()
CRED = HOME / "linkedin/.credentials"

args = [a for a in sys.argv[1:] if not a.startswith("--")]
DRY_RUN = "--dry-run" in sys.argv
QUEUE = pathlib.Path(args[0]) if args else HOME / "linkedin/QUEUE/2026-09-28.md"

# LinkedIn little-format reserved characters (user-confirmed set, minus '#'):
# | { } @ [ ] ( ) < > \ * _ ~
RESERVED = set('|{}@[]()<>\\*_~')


def escape_linkedin_little(text: str) -> str:
    out = []
    for ch in text:
        if ch in RESERVED:
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


creds = {}
for line in CRED.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    creds[k.strip()] = v.strip().strip('"').strip("'")

TOKEN = creds.get("ACCESS_TOKEN")
if not TOKEN:
    print("RESULT=NO_TOKEN")
    sys.exit(1)

raw = QUEUE.read_text()
m = re.search(r"POST TEXT:\s*\n(.*?)\n---", raw, re.S)
if not m:
    print("RESULT=NO_POST_TEXT")
    sys.exit(1)

post_text_raw = m.group(1).strip()
post_text = escape_linkedin_little(post_text_raw)

print("RAW:")
print(repr(post_text_raw))
print()
print("ESCAPED:")
print(repr(post_text))
print()
print("RAW LEN:", len(post_text_raw))
print("ESCAPED LEN:", len(post_text))
if "\u2014" in post_text or "\u2013" in post_text:
    print("WARN: long dash found in post text")
print()


def api(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": "202609",
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()
    except Exception as e:
        return 0, {}, f"EXC: {e!r}"


if DRY_RUN:
    sub = "<member-sub>"  # заполнить из GET /v2/userinfo (не публиковать реальный sub)
    payload = {
        "author": f"urn:li:person:{sub}",
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    print("COMMENTARY (final, repr):")
    print(repr(payload["commentary"]))
    print()
    print("FULL JSON PAYLOAD:")
    print(json.dumps(payload))
    print()
    print("RESULT=DRY_RUN - no network call made")
    sys.exit(0)

# live path
st, hd, body = api("https://api.linkedin.com/v2/userinfo")
print(f"USERINFO_HTTP={st}")
if st != 200:
    print("BODY:", body[:400])
    print("RESULT=USERINFO_FAIL")
    sys.exit(2)
sub = json.loads(body)["sub"]
print(f"SUB_OK={'yes' if sub else 'no'}")

payload = {
    "author": f"urn:li:person:{sub}",
    "commentary": post_text,
    "visibility": "PUBLIC",
    "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                     "thirdPartyDistributionChannels": []},
    "lifecycleState": "PUBLISHED",
    "isReshareDisabledByAuthor": False,
}
st2, hd2, body2 = api("https://api.linkedin.com/rest/posts", "POST", payload)
print(f"PUBLISH_HTTP={st2}")
urn = hd2.get("x-restli-id") or hd2.get("X-Restli-Id") or ""
print(f"URN={urn}")
if st2 != 201:
    print("BODY:", body2[:500])
    print("RESULT=PUBLISH_FAIL")
    sys.exit(3)
print("RESULT=PUBLISHED")
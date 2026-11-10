#!/usr/bin/env python3
"""Collect user fills (and optional funding) for two public Hyperliquid wallets
via the official read-only info endpoint. READ-ONLY, no auth, no trading.

Window (fixed): [2026-11-03T00:00:00Z, 2026-11-09T23:59:59.999Z] UTC.
POST budget: <=20 total across the whole run.
On 429/403/5xx -> hard stop and exit non-zero.
"""
import json
import sys
import time
import urllib.request

API = "https://api.hyperliquid.xyz/info"

WALLETS = {
    "A_bobbybigsize": "0x7fdafde5cfb5465924316eced2d3715494c517d1",
    "B_vike": "0x77375a8c9d13bf79afb2a87f1b0ac1dfd5f5bf66",
}

WINDOW_START = 1793664000000  # 2026-11-03T00:00:00Z
WINDOW_END = 1794268799999    # 2026-11-09T23:59:59.999Z

MAX_POSTS = 20
posts_used = 0


def post(req):
    global posts_used
    if posts_used >= MAX_POSTS:
        raise SystemExit(f"BUDGET_EXHAUSTED after {posts_used} POSTs")
    body = json.dumps(req).encode("utf-8")
    r = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            status = resp.status
            data = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        data = e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"NETWORK_ERROR: {e.reason}")
    posts_used += 1
    if status in (429, 403) or status >= 500:
        raise SystemExit(f"HTTP_ERROR {status} on {req.get('type')} for {req.get('user','')}: {data[:300]!r}")
    if status != 200:
        raise SystemExit(f"UNEXPECTED_HTTP {status} on {req.get('type')}: {data[:300]!r}")
    return json.loads(data)


def main():
    global posts_used
    out = {
        "meta": {
            "api": API,
            "window_start_utc_ms": WINDOW_START,
            "window_end_utc_ms": WINDOW_END,
            "window_iso": "[2026-11-03T00:00:00Z, 2026-11-09T23:59:59.999Z]",
            "max_posts_budget": MAX_POSTS,
        },
        "wallets": {},
    }
    for key, addr in WALLETS.items():
        print(f"== {key} {addr} ==", flush=True)
        fills = post({"type": "userFillsByTime", "user": addr,
                      "startTime": WINDOW_START, "endTime": WINDOW_END})
        print(f"  userFillsByTime returned {len(fills)} fills", flush=True)
        funded = []
        try:
            funded = post({"type": "userFunding", "user": addr,
                           "startTime": WINDOW_START, "endTime": WINDOW_END})
            print(f"  userFunding returned {len(funded)} entries", flush=True)
        except SystemExit as e:
            if "HTTP_ERROR 4" in str(e):
                print(f"  userFunding unavailable/errored: {e}", flush=True)
                funded = None
            else:
                raise
        out["wallets"][key] = {
            "address": addr,
            "fills": fills,
            "funding": funded,
            "posts_for_wallet": posts_used,
        }
    with open("raw_fills.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"POSTs used total: {posts_used}", flush=True)
    with open("posts_used.txt", "w") as f:
        f.write(str(posts_used))
    print("DONE")


if __name__ == "__main__":
    main()
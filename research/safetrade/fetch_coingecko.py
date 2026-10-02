#!/usr/bin/env python3
"""Fetch CoinGecko exchange list (spot, trust-score-agnostic), save raw JSON, rank by 24h spot volume."""
import json, time, urllib.request, sys, datetime, os

OUT = os.path.expanduser("~/safetrade-research/data/raw/coingecko_exchanges")
os.makedirs(OUT, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
      "Accept": "application/json"}
BASE = "https://api.coingecko.com/api/v3/exchanges?per_page=250&page={}"

all_ex = []
for page in (1, 2, 3):
    url = BASE.format(page)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        chunk = json.loads(r.read())
    if not chunk:
        break
    all_ex.extend(chunk)
    print(f"page {page}: {len(chunk)}", file=sys.stderr)
    time.sleep(1.2)  # polite rate-limit

ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
raw = {"fetched_utc": ts, "url": BASE, "count": len(all_ex), "exchanges": all_ex}
with open(f"{OUT}/raw_exchanges_{ts[:10]}.json", "w") as f:
    json.dump(raw, f, indent=1, ensure_ascii=False)
print(f"total={len(all_ex)} saved={OUT}/raw_exchanges_{ts[:10]}.json")
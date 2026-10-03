#!/usr/bin/env python3
"""Common pairs: SafeTrade markets vs candidate exchanges (via REST). Output CSV."""
import json, urllib.request, sys, time, os
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import ccxt

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def load_exchange_usdt(path="/home/kali/safetrade-research/data/analysis/common_pairs.json"):
    """Публичные USDT-базы Binance/OKX/Bybit из сохранённого common_pairs.json.

    Возвращает (set_binance, set_okx, set_bybit) — множества BASE (без пары).
    Если файла нет или он пуст — ({}, {}, {}).
    БЕЗ сетевых вызовов (файл собирается отдельным скриптом).
    """
    if not os.path.exists(path):
        return ({}, {}, {})
    data = json.load(open(path))
    outs = data.get("candidates", {})
    def _bases(name):
        bases = set()
        for s in outs.get(name, []):
            base = s.split("/")[0].split(":")[0]
            bases.add(base)
        return bases
    return (_bases("binance"), _bases("okx"), _bases("bybit"))


if __name__ == "__main__":
    # SafeTrade markets
    st = get("https://safetrade.com/api/v2/trade/public/markets")
    st_mkts = {}
    st_usdt = {}
    for m in st:
        base, quote = m["base_unit"].upper(), m["quote_unit"].upper()
        st_mkts[f"{base}/{quote}"] = m
        if quote == "USDT" and m.get("state", "") in ("enabled", "ON", ""):
            st_usdt[base] = m
    print(f"SafeTrade markets total={len(st_mkts)} usdt-pairs={len(st_usdt)}")
    # BTC, ETH, and top affordable: check ticks
    for b in ["BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "ADA"]:
        if b in st_usdt:
            m = st_usdt[b]
            print(f"  {b}/USDT min_price={m.get('min_price')} amount_prec={m.get('amount_precision')} price_prec={m.get('price_precision')} state={m.get('state')}")

    pairs_safe = set(st_usdt.keys())

    # Candidate exchange symbols
    cands = {
        "binance": "BTC/USDT:USDT",
        "okx": "BTC/USDT",
        "bybit": "BTC/USDT:USDT",
        "gate": "BTC/USDT",
        "mexc": "BTC/USDT",
        "coinbase": "BTC/USD",
    }
    outs = {}
    for name, sym in cands.items():
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True})
            markets = ex.load_markets()
            usdt = {s.split("/")[0] for s in markets if s.endswith("/USDT")}
            common = sorted(pairs_safe & usdt)
            outs[name] = common
            print(f"{name:<10} markets={len(markets)} usdt={len(usdt)} common_with_safe={len(common)}")
            print(f"   sample: {common[:25]}")
        except Exception as e:
            print(f"{name:<10} FAIL {type(e).__name__} {str(e)[:120]}")
        time.sleep(0.5)

    os.makedirs(os.path.dirname("/home/kali/safetrade-research/data/analysis/common_pairs.json"), exist_ok=True)
    json.dump({"safe_usdt": sorted(pairs_safe), "candidates": outs},
              open("/home/kali/safetrade-research/data/analysis/common_pairs.json", "w"), indent=1)
    print("saved common_pairs.json")
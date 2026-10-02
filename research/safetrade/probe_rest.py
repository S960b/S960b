#!/usr/bin/env python3
"""Probe public REST endpoints of candidate exchanges. Timestamps + HTTP status + sample fields."""
import json, time, urllib.request, urllib.error, ssl, datetime

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
      "Accept": "application/json"}
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

PROBES = [
    # (exchange, label, url, json_path_to_check)
    ("binance", "ticker", "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT", ["bidPrice"]),
    ("binance", "time",   "https://api.binance.com/api/v3/time", ["serverTime"]),
    ("coinup", "ticker",  "https://api.coinup.io/api/v1/spot/market/ticker?symbol=BTCUSDT", None),
    ("coinup", "ping",    "https://api.coinup.io/api/v1/ping", None),
    ("btcc",   "ticker",  "https://api.btcc.com/api/v3/ticker/BTCUSDT", None),
    ("pionex", "ticker",  "https://api.pionex.com/api/v1/market/tickers", None),
    ("tapbit", "ticker",  "https://api.tapbit.com/api/spot/v1/quote/tickers", None),
    ("okx",    "ticker",  "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT", ["data"]),
    ("weex",   "ticker",  "https://api.weex.com/api/v3/spot/market/ticker?symbol=BTCUSDT", None),
    ("lbank",  "ticker",  "https://api.lbank.com/api/spot/v1/public/ticker?symbol=btc_usdt", None),
    ("bybit",  "ticker",  "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT", ["result"]),
    ("gate",   "ticker",  "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT", None),
    ("mexc",   "ticker",  "https://api.mexc.com/api/v3/ticker/bookTicker?symbol=BTCUSDT", None),
    ("safetrade","ticker","https://safetrade.com/api/v1/ticker?symbol=BTCUSDT", None),
    ("safetrade","ping",  "https://safetrade.com/api/v1/ping", None),
    ("safetrade","base",  "https://safetrade.com/api", None),
]

def probe(ex, label, url, check):
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            body = r.read(2000)
            dt = (time.monotonic() - t0) * 1000
            status = r.status
            ctype = r.headers.get("Content-Type", "")
        txt = body.decode("utf-8", "replace")[:300].replace("\n", " ")
        ok = "?"
        if check:
            try:
                j = json.loads(body)
                cur = j
                for k in check:
                    cur = cur[k]
                ok = "OK" if cur else "EMPTY"
            except Exception:
                ok = "PARSE_FAIL"
        print(f"{ex:<12} {label:<8} http={status:<3} {dt:6.0f}ms ctype={ctype[:30]:<30} check={ok} body={txt[:160]}")
    except urllib.error.HTTPError as e:
        dt = (time.monotonic() - t0) * 1000
        try:
            b = e.read(300).decode("utf-8", "replace").replace("\n", " ")
        except Exception:
            b = ""
        print(f"{ex:<12} {label:<8} HTTPERR {e.code:<3} {dt:6.0f}ms body={b[:200]}")
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        print(f"{ex:<12} {label:<8} EXC     {dt:6.0f}ms {type(e).__name__}: {str(e)[:120]}")

print(f"probe started {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
for p in PROBES:
    probe(*p)
    time.sleep(0.4)
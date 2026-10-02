#!/usr/bin/env python3
"""SafeTrade WS with retries + realistic headers."""
import asyncio, json, sys, time
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import websockets

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
URI = "wss://safe.trade/api/v2/websocket/public"

async def attempt(uri, headers):
    async with websockets.connect(uri, open_timeout=15, user_agent_header=UA,
                                  additional_headers=headers, close_timeout=3) as ws:
        sub = {"event": "subscribe", "streams": ["global.tickers", "btcusdt.depth", "btcusdt.trades"]}
        await ws.send(json.dumps(sub))
        t0 = time.monotonic()
        n = 0
        sample = []
        while time.monotonic() - t0 < 12:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                continue
            n += 1
            if n <= 3:
                sample.append(str(m)[:150])
            d = json.loads(m)
            if "success" in d:
                continue
            if len(sample) >= 3:
                break
        return n, sample

async def main():
    combos = [
        ("origin=safetrade.com", {"Origin": "https://safetrade.com", "Accept-Encoding": "gzip"}),
        ("origin=safe.trade", {"Origin": "https://safe.trade", "Accept-Encoding": "gzip"}),
        ("none", {"Accept-Encoding": "gzip"}),
        ("origin+secws", {"Origin": "https://safetrade.com", "Sec-WebSocket-Version": "13", "Accept-Encoding": "gzip"}),
    ]
    for name, h in combos:
        for i in range(2):
            try:
                n, sample = await attempt(URI, h)
                print(f"[{name}] try{i}: msgs={n}")
                for s in sample:
                    print(f"    {s}")
                if n:
                    print(f"    -> OK with {name}")
                    return
            except Exception as e:
                print(f"[{name}] try{i}: {type(e).__name__}: {str(e)[:90]}")
            await asyncio.sleep(1)
    print("ALL FAILED")

asyncio.run(main())
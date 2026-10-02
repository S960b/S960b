#!/usr/bin/env python3
"""SafeTrade WS: capture depth + trades messages, print full raw structure."""
import asyncio, json, sys
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import websockets

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
URI = "wss://safe.trade/api/v2/websocket/public"

async def connect_with_retry(uri, attempts=4):
    last = None
    for i in range(attempts):
        try:
            return await websockets.connect(uri, open_timeout=15, user_agent_header=UA,
                                            additional_headers={"Origin": "https://safetrade.com",
                                                                "Accept-Encoding": "gzip"})
        except Exception as e:
            last = e
            await asyncio.sleep(1.5 + i)
    raise last

async def main():
    ws = await connect_with_retry(URI)
    sub = {"event": "subscribe", "streams": ["btcusdt.depth", "btcusdt.trades", "ethusdt.depth"]}
    await ws.send(json.dumps(sub))
    t0 = asyncio.get_event_loop().time()
    counts = {}
    full_depth = None
    full_trades = None
    while asyncio.get_event_loop().time() - t0 < 25:
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=25)
        except asyncio.TimeoutError:
            print("timeout"); break
        d = json.loads(m)
        for k in ("btcusdt.depth", "btcusdt.trades", "ethusdt.depth"):
            if k in d:
                counts[k] = counts.get(k, 0) + 1
                if k == "btcusdt.depth" and full_depth is None:
                    full_depth = d
                if k == "btcusdt.trades" and full_trades is None:
                    full_trades = d
    print("counts:", counts)
    print("FULL DEPTH MSG:", json.dumps(full_depth, ensure_ascii=False)[:1200])
    print()
    print("FULL TRADES MSG:", json.dumps(full_trades, ensure_ascii=False)[:1200])
    await ws.close()

asyncio.run(main())
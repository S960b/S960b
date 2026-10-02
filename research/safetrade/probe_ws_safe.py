#!/usr/bin/env python3
"""SafeTrade WS: try multiple host/path combos."""
import asyncio, json, sys
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import websockets

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
SUB = {"event": "subscribe", "streams": ["global.tickers", "btcusdt.depth"]}

URLS = [
    "wss://safetrade.com/api/v2/websocket/public",
    "wss://safetrade.com/api/v2/websocket",
    "wss://safe.trade/api/v2/websocket/public",
    "wss://www.safetrade.com/api/v2/websocket/public",
    "wss://api.zsmartex.com/api/v2/websocket/public",
    "wss://websocket.safetrade.com/public",
    "wss://safetrade.com/websocket/public",
]

async def probe(uri):
    try:
        async with websockets.connect(uri, open_timeout=8, user_agent_header=UA,
                                      ping_interval=None, close_timeout=3) as ws:
            print(f"{uri:<60} CONN_OK")
            await ws.send(json.dumps(SUB))
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=6)
                print(f"    SUB_ACK: {str(m)[:160]}")
            except asyncio.TimeoutError:
                print("    no msg in 6s")
    except Exception as e:
        print(f"{uri:<60} FAIL {type(e).__name__}: {str(e)[:110]}")

async def main():
    for u in URLS:
        await probe(u)
        await asyncio.sleep(0.3)

asyncio.run(main())
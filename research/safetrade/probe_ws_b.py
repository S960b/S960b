#!/usr/bin/env python3
"""Probe Pionex, WEEX, MEXC WS + SafeTrade trades channel."""
import asyncio, json, sys, time
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import websockets

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

async def probe(name, uri, sub, extra_headers=None, timeout=15):
    try:
        async with websockets.connect(uri, open_timeout=10, user_agent_header=UA,
                                      additional_headers=extra_headers or {}) as ws:
            await ws.send(json.dumps(sub))
            t0 = time.monotonic()
            msgs = []
            while time.monotonic() - t0 < timeout and len(msgs) < 3:
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=3)
                    msgs.append(str(m)[:160])
                except asyncio.TimeoutError:
                    continue
            print(f"{name:<16} OK msgs={len(msgs)}")
            for m in msgs:
                print(f"    {m}")
    except Exception as e:
        print(f"{name:<16} FAIL {type(e).__name__}: {str(e)[:130]}")

async def main():
    # Pionex WS (doc: wss://api.pionex.com/ws)
    await probe("pionex", "wss://api.pionex.com/ws",
                {"op": "subscribe", "args": ["market:BTC_USDT.TRADE", "market:BTC_USDT.DEPTH"]})
    # WEEX WS v3 (doc host ws-spot.weex.com)
    await probe("weex", "wss://ws-spot.weex.com",
                {"method": "SUBSCRIBE", "params": ["spot@public.books.depth.step0@BTCUSDT"],
                 "id": 1}, extra_headers={"Origin": "https://www.weex.com", "Referer": "https://www.weex.com/"})
    # MEXC WS second attempt
    await probe("mexc", "wss://wbs.mexc.com/ws",
                {"method": "SUBSCRIPTION", "params": ["spot@public.bookTicker.v3.api@BTCUSDT"]})
    # SafeTrade trades channel (with retries)
    for i in range(3):
        try:
            async with websockets.connect("wss://safe.trade/api/v2/websocket/public",
                                          open_timeout=15, user_agent_header=UA,
                                          additional_headers={"Origin": "https://safetrade.com"}) as ws:
                await ws.send(json.dumps({"event": "subscribe", "streams": ["btcusdt.trades"]}))
                t0 = time.monotonic()
                got = None
                while time.monotonic() - t0 < 20:
                    m = await asyncio.wait_for(ws.recv(), timeout=20)
                    d = json.loads(m)
                    if "btcusdt.trades" in d:
                        got = d["btcusdt.trades"]
                        break
                print(f"safetrade_trades msgs: {json.dumps(got, ensure_ascii=False)[:400] if got else None}")
                break
        except Exception as e:
            print(f"safetrade_trades try{i}: {type(e).__name__} {str(e)[:80]}")
            await asyncio.sleep(2)

asyncio.run(main())
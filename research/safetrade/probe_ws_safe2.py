#!/usr/bin/env python3
"""SafeTrade WS: verify real market data flows (global.tickers, btcusdt.depth, btcusdt.trades)."""
import asyncio, json, sys, time
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import websockets

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
URI = "wss://safe.trade/api/v2/websocket/public"

async def main():
    async with websockets.connect(URI, open_timeout=10, user_agent_header=UA) as ws:
        sub = {"event": "subscribe", "streams": ["global.tickers", "btcusdt.depth", "btcusdt.trades"]}
        await ws.send(json.dumps(sub))
        by_stream = {}
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=15)
            except asyncio.TimeoutError:
                print("no data for 15s"); break
            try:
                d = json.loads(m)
            except Exception:
                print("NONJSON:", str(m)[:120]); continue
            if "success" in d:
                print("ACK:", json.dumps(d)[:150]); continue
            keys = [k for k in d.keys() if k != "event"]
            for k in keys:
                by_stream[k] = by_stream.get(k, 0) + 1
            if len(by_stream) >= 3 and sum(by_stream.values()) >= 6:
                break
        print("messages per stream:", by_stream)
        # sample one depth msg
        if "btcusdt.depth" in by_stream:
            pass
        total = sum(by_stream.values())
        print(f"total msgs in {time.monotonic()-t0:.1f}s: {total}")
        # Now show one raw message of each type by re-receiving
        # (we already consumed; simplest: re-subscribe and capture first of each)
        by_stream2 = {}
        sub2 = {"event": "subscribe", "streams": ["btcusdt.depth"]}
        await ws.send(json.dumps(sub2))
        t1 = time.monotonic()
        got_depth = None
        while time.monotonic() - t1 < 8 and got_depth is None:
            m = await asyncio.wait_for(ws.recv(), timeout=8)
            d = json.loads(m)
            if "btcusdt.depth" in d:
                got_depth = d["btcusdt.depth"]
        print("DEPTH SAMPLE:", json.dumps(got_depth)[:400] if got_depth else None)

asyncio.run(main())
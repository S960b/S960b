#!/usr/bin/env python3
"""WS smoke test: SafeTrade + candidate exchanges. Connect, subscribe, measure time to first msg, gather sample."""
import asyncio, json, sys, time, datetime
sys.path.insert(0, "/home/kali/safetrade-research/venv/lib/python3.13/site-packages")
import websockets

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

TARGETS = [
    ("safetrade_public", "wss://safetrade.com/api/v2/websocket/public",
     {"event": "subscribe", "streams": ["global.tickers", "btcusdt.depth"]}, ["global.tickers", "btcusdt.depth"]),
    ("binance", "wss://stream.binance.com:9443/ws",
     {"method": "SUBSCRIBE", "params": ["btcusdt@bookTicker", "btcusdt@depth20@100ms"], "id": 1}, ["bookTicker", "depthUpdate"]),
    ("okx", "wss://ws.okx.com:8443/ws/v5/public",
     {"op": "subscribe", "args": [{"channel": "books5", "instId": "BTC-USDT"}, {"channel": "tickers", "instId": "BTC-USDT"}]}, ["books5", "tickers"]),
    ("bybit", "wss://stream.bybit.com/v5/public/spot",
     {"op": "subscribe", "args": ["orderbook.1.BTCUSDT", "publicTrade.BTCUSDT"]}, ["orderbook", "trade"]),
    ("gate", "wss://api.gateio.ws/ws/v4/",
     {"time": int(time.time()), "channel": "spot.book_ticker", "event": "subscribe", "payload": ["BTC_USDT"]}, ["book_ticker"]),
    ("mexc", "wss://wbs.mexc.com/ws",
     {"method": "SUBSCRIPTION", "params": ["spot@public.bookTicker.v3.api@BTCUSDT"]}, ["bookTicker"]),
]

async def probe(name, uri, sub, expect):
    t0 = time.monotonic()
    msgs = []
    try:
        async with websockets.connect(uri, open_timeout=10, user_agent_header=UA, ping_interval=20, ping_timeout=10) as ws:
            conn_ms = (time.monotonic() - t0) * 1000
            await ws.send(json.dumps(sub))
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and len(msgs) < 3:
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=2)
                    msgs.append(m)
                except asyncio.TimeoutError:
                    continue
            dt = (time.monotonic() - t0) * 1000
            sample = " | ".join(str(m)[:110] for m in msgs)
            print(f"{name:<18} CONN {conn_ms:6.0f}ms first_msgs={len(msgs)} total={dt:6.0f}ms\n    {sample}")
    except Exception as e:
        print(f"{name:<18} FAIL {type(e).__name__}: {str(e)[:140]}")

async def main():
    print(f"WS probe {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    for t in TARGETS:
        await probe(*t)
        await asyncio.sleep(0.5)

asyncio.run(main())
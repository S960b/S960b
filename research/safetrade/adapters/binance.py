"""Binance spot adapter. REST v3 + WS stream.binance.com:9443.
Каналы: bookTicker (BBO), depth20@100ms (partial book = снапшот top-20)."""
import asyncio
import json
import logging
import time
import urllib.request

from .base import BaseAdapter, UA
from .events import make_event

log = logging.getLogger(__name__)

REST = "https://api.binance.com/api/v3"
WS = "wss://stream.binance.com:9443/ws"


class BinanceAdapter(BaseAdapter):
    exchange = "binance"

    def __init__(self, markets_cfg=None, channels=None, raw_q=None):
        super().__init__(markets_cfg, channels)
        self.raw_q = raw_q

    # ---- REST ----
    def discover_markets(self) -> list:
        req = urllib.request.Request(f"{REST}/exchangeInfo", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            info = json.loads(r.read())
        out = []
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING" or s.get("quoteAsset", "").upper() != "USDT":
                continue
            if s.get("isSpotTradingAllowed") is False:
                continue
            out.append({
                "exchange": "binance", "symbol": f"{s['baseAsset']}/USDT",
                "base": s["baseAsset"], "quote": "USDT",
                "native": s["symbol"], "type": "spot", "status": s.get("status"),
                "price_precision": s.get("quotePrecision"), "min_qty": s.get("filters", [{}])[0].get("minQty"),
            })
        return out

    # ---- WS ----
    async def stream(self, symbol: str, sink: asyncio.Queue, run_id: str, boot_id: str) -> None:
        base = symbol.split("/")[0]
        native = f"{base}USDT"
        streams = []
        if self.channels.get("bbo"):
            streams.append(f"{native.lower()}@bookTicker")
        if self.channels.get("book"):
            streams.append(f"{native.lower()}@depth20@100ms")
        if not streams:
            return
        while True:
            try:
                ws = await self.connect(WS)
                self._mark_reconnect()
                await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))
                while True:
                    raw, parsed, t_utc, t_mono = await self._ws_recv_msg(ws)
                    self._push_raw(symbol, raw, t_utc, t_mono)
                    if isinstance(parsed, dict) and parsed.get("result") is None and "id" in parsed:
                        continue  # sub ack
                    ev = self._parse(native, symbol, parsed, t_utc, t_mono, run_id, boot_id)
                    if ev:
                        await sink.put(ev)
                        self._mark_msg()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mark_error(f"{type(e).__name__}: {str(e)[:100]}")
                log.warning("binance %s disconnect: %s", symbol, e)
                await asyncio.sleep(3)

    def _parse(self, native, symbol, parsed, t_utc, t_mono, run_id, boot_id):
        if not isinstance(parsed, dict):
            return None
        etype = parsed.get("e")
        if etype == "bookTicker" or ("b" in parsed and "a" in parsed and "u" in parsed):
            # bookTicker в raw-канале не содержит "e" (только в combined stream)
            bbo = {"bid_price": parsed.get("b"), "bid_qty": parsed.get("B"),
                   "ask_price": parsed.get("a"), "ask_qty": parsed.get("A")}
            return make_event(
                "binance", symbol.replace("/", ""), native, "bbo", recv_utc=t_utc, recv_mono=t_mono,
                exchange_event_ts=None, sequence=parsed.get("u"),
                side="bid", price=bbo["bid_price"], qty=bbo["bid_qty"],
                run_id=run_id, boot_id=boot_id, payload=bbo,
            )
        if etype == "depthUpdate" or ("bids" in parsed and "asks" in parsed):
            # depth20@100ms: полный снапшот top-20; поле ключа lastUpdateId (без "e"/"u")
            return make_event(
                "binance", symbol.replace("/", ""), native, "book_snapshot", recv_utc=t_utc, recv_mono=t_mono,
                exchange_event_ts=None, sequence=parsed.get("u") or parsed.get("lastUpdateId"),
                bids=[[p, q] for p, q in parsed.get("bids", [])],
                asks=[[p, q] for p, q in parsed.get("asks", [])],
                run_id=run_id, boot_id=boot_id, payload=parsed,
            )
        return None

    def _push_raw(self, symbol, raw, t_utc, t_mono):
        if self.raw_q is not None:
            try:
                self.raw_q.put_nowait({"exchange": "binance", "symbol": symbol, "raw": raw,
                                       "recv_utc_ns": t_utc, "recv_mono_ns": t_mono})
            except asyncio.QueueFull:
                pass

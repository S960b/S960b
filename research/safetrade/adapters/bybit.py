"""Bybit spot adapter. REST /v5 + WS stream.bybit.com/v5/public/spot.
Каналы: orderbook.1 (BBO), orderbook.50 (snapshot+delta top-50)."""
import asyncio
import json
import logging
import time
import urllib.request

from .base import BaseAdapter, UA
from .events import make_event

log = logging.getLogger(__name__)

REST = "https://api.bybit.com/v5"
WS = "wss://stream.bybit.com/v5/public/spot"


class BybitAdapter(BaseAdapter):
    exchange = "bybit"

    def __init__(self, markets_cfg=None, channels=None, raw_q=None):
        super().__init__(markets_cfg, channels)
        self.raw_q = raw_q

    def discover_markets(self) -> list:
        req = urllib.request.Request(f"{REST}/market/instruments-info?category=spot",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            info = json.loads(r.read())
        out = []
        for s in info.get("result", {}).get("list", []):
            if s.get("status") != "Trading" or s.get("quoteCoin", "").upper() != "USDT":
                continue
            out.append({
                "exchange": "bybit", "symbol": f"{s['baseCoin']}/USDT",
                "base": s["baseCoin"], "quote": "USDT",
                "native": s["symbol"], "type": "spot", "status": s.get("status"),
                "price_precision": s.get("priceFilter", {}).get("tickSize"),
                "min_qty": s.get("lotSizeFilter", {}).get("minOrderQty"),
            })
        return out

    async def stream(self, symbol: str, sink: asyncio.Queue, run_id: str, boot_id: str) -> None:
        native = symbol.replace("/", "")
        topics = []
        if self.channels.get("bbo"):
            topics.append(f"orderbook.1.{native}")
        if self.channels.get("book"):
            topics.append(f"orderbook.50.{native}")
        if not topics:
            return
        got_snapshot = {t: False for t in topics}
        while True:
            try:
                ws = await self.connect(WS)
                self._mark_reconnect()
                await ws.send(json.dumps({"op": "subscribe", "args": topics}))
                while True:
                    raw, parsed, t_utc, t_mono = await self._ws_recv_msg(ws)
                    self._push_raw(symbol, raw, t_utc, t_mono)
                    if isinstance(parsed, dict) and parsed.get("op") == "subscribe":
                        continue
                    ev = self._parse(symbol, native, parsed, t_utc, t_mono, run_id, boot_id)
                    if ev:
                        topic = parsed.get("topic", "")
                        mtype = parsed.get("type", "snapshot")
                        if mtype == "snapshot":
                            got_snapshot[topic] = True
                        elif mtype == "delta" and not got_snapshot.get(topic, False):
                            ev["quality_flags"].append("no_snapshot_yet")
                        await sink.put(ev)
                        self._mark_msg()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mark_error(f"{type(e).__name__}: {str(e)[:100]}")
                log.warning("bybit %s disconnect: %s", symbol, e)
                await asyncio.sleep(3)

    def _parse(self, symbol, native, parsed, t_utc, t_mono, run_id, boot_id):
        if not isinstance(parsed, dict):
            return None
        topic = parsed.get("topic", "")
        data = parsed.get("data", {})
        if topic.startswith("orderbook.1"):
            # BBO: b=[["price","size"]], a=[["price","size"]] (Bybit orderbook.1)
            b = data.get("b", [[]])[0]
            a = data.get("a", [[]])[0]
            bbo = {"bid_price": b[0] if b else None, "bid_qty": b[1] if len(b) > 1 else None,
                   "ask_price": a[0] if a else None, "ask_qty": a[1] if len(a) > 1 else None}
            return make_event(
                "bybit", symbol.replace("/", ""), native, "bbo", recv_utc=t_utc, recv_mono=t_mono,
                exchange_event_ts=data.get("cts") or data.get("ts"), sequence=data.get("u"),
                side="bid", price=bbo["bid_price"], qty=bbo["bid_qty"],
                run_id=run_id, boot_id=boot_id, payload=bbo,
            )
        if topic.startswith("orderbook."):
            mtype = parsed.get("type", "snapshot")
            et = "book_snapshot" if mtype == "snapshot" else "book_delta"
            # delta: delete = replace обновление; Bybit: массив [price, size], size=0 → удалить
            return make_event(
                "bybit", symbol.replace("/", ""), native, et, recv_utc=t_utc, recv_mono=t_mono,
                exchange_event_ts=data.get("cts") or data.get("ts"), sequence=data.get("u"),
                bids=data.get("b"), asks=data.get("a"),
                run_id=run_id, boot_id=boot_id, payload={"type": mtype, "u": data.get("u"), "seq": data.get("seq")},
            )
        return None

    def _push_raw(self, symbol, raw, t_utc, t_mono):
        if self.raw_q is not None:
            try:
                self.raw_q.put_nowait({"exchange": "bybit", "symbol": symbol, "raw": raw,
                                       "recv_utc_ns": t_utc, "recv_mono_ns": t_mono})
            except asyncio.QueueFull:
                pass

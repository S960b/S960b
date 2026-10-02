"""OKX spot adapter. REST /api/v5 + WS ws.okx.com:8443/ws/v5/public.
Каналы: tickers (BBO), books5 (snapshot+delta, top-5)."""
import asyncio
import json
import logging
import time
import urllib.request

from .base import BaseAdapter, UA
from .events import make_event

log = logging.getLogger(__name__)

REST = "https://www.okx.com/api/v5"
WS = "wss://ws.okx.com/ws/v5/public"


class OKXAdapter(BaseAdapter):
    exchange = "okx"

    def __init__(self, markets_cfg=None, channels=None, raw_q=None):
        super().__init__(markets_cfg, channels)
        self.raw_q = raw_q

    def discover_markets(self) -> list:
        req = urllib.request.Request(f"{REST}/public/instruments?instType=SPOT",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            info = json.loads(r.read())
        out = []
        for s in info.get("data", []):
            if s.get("state") != "live" or s.get("quoteCcy", "").upper() != "USDT":
                continue
            out.append({
                "exchange": "okx", "symbol": f"{s['baseCcy']}/USDT",
                "base": s["baseCcy"], "quote": "USDT",
                "native": s["instId"], "type": "spot", "status": s.get("state"),
                "price_precision": s.get("tickSz"), "min_qty": s.get("minSz"),
            })
        return out

    async def stream(self, symbol: str, sink: asyncio.Queue, run_id: str, boot_id: str) -> None:
        inst = symbol.replace("/", "-")
        args = []
        if self.channels.get("bbo"):
            args.append({"channel": "tickers", "instId": inst})
        if self.channels.get("book"):
            args.append({"channel": "books5", "instId": inst})
        if not args:
            return
        while True:
            ws = None
            try:
                ws = await self.connect(WS)
                self._mark_reconnect()
                await ws.send(json.dumps({"op": "subscribe", "args": args}))
                first = True
                while True:
                    raw, parsed, t_utc, t_mono = await self._ws_recv_msg(ws)
                    self._push_raw(symbol, raw, t_utc, t_mono)
                    if isinstance(parsed, dict) and parsed.get("event") == "subscribe":
                        continue
                    ev = self._parse(symbol, inst, parsed, t_utc, t_mono, run_id, boot_id)
                    if ev:
                        if first and ev["event_type"] == "book_delta" and self.channels.get("book"):
                            # до снапшота дельту не применяем (помечаем quality)
                            ev["quality_flags"].append("no_snapshot_yet")
                        await sink.put(ev)
                        first = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mark_error(f"{type(e).__name__}: {str(e)[:100]}")
                log.warning("okx %s disconnect: %s", symbol, e)
                await self._emit_reset(sink, symbol, run_id, boot_id)
                await asyncio.sleep(3)
            finally:
                if ws is not None:
                    await ws.close()

    def _parse(self, symbol, inst, parsed, t_utc, t_mono, run_id, boot_id):
        if not isinstance(parsed, dict):
            return None
        ch = parsed.get("arg", {}).get("channel") if isinstance(parsed.get("arg"), dict) else None
        data = parsed.get("data", [])
        if not data:
            return None
        if ch == "tickers":
            d = data[0]
            bbo = {"bid_price": d.get("bidPx"), "bid_qty": d.get("bidSz"),
                   "ask_price": d.get("askPx"), "ask_qty": d.get("askSz")}
            return make_event(
                "okx", symbol.replace("/", ""), inst, "bbo", recv_utc=t_utc, recv_mono=t_mono,
                exchange_event_ts=int(d.get("ts", 0)) or None, sequence=None,
                side="bid", price=bbo["bid_price"], qty=bbo["bid_qty"],
                run_id=run_id, boot_id=boot_id, payload=bbo,
            )
        if ch == "books5":
            d = data[0]
            # OKX books5: полный срез top-5 в каждом сообщении; action может отсутствовать
            # (по документации первое — snapshot, далее update с полным списком уровней).
            action = d.get("action", "snapshot")
            et = "book_snapshot"
            bids = [[p[0], p[1]] for p in d.get("bids", [])]
            asks = [[p[0], p[1]] for p in d.get("asks", [])]
            return make_event(
                "okx", symbol.replace("/", ""), inst, et, recv_utc=t_utc, recv_mono=t_mono,
                exchange_event_ts=int(d.get("ts", 0)) or None, sequence=d.get("seqId"),
                bids=bids, asks=asks,
                run_id=run_id, boot_id=boot_id, payload={"action": action, "bids": d.get("bids"), "asks": d.get("asks")},
            )
        return None

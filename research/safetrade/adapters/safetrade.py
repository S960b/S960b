"""SafeTrade adapter v2 (ревью P0-2/P0-3).
- REST через aiohttp в to_thread — не блокирует event loop.
- WS depth: delta с sequence; контроль монотонности; gap -> invalid до нового REST-снимка.
- REST-снимки периодически (rest_snapshot_s) как отдельный источник наблюдений с RTT;
  книга помечается quality_flags=['rest_provisional'], т.к. доказуемого объединения
  snapshot+delta без общей seq нет (rev. P0-3: не объявлять synchronized).
- Health: liveness (recv), subscription, последний снапшот, применяемые дельты, gaps.
WS: wss://safe.trade/api/v2/websocket/public (Cloudflare флаки — ретраи с Origin)."""
import asyncio
import json
import logging
import time
import urllib.request

import aiohttp

from .base import BaseAdapter, UA
from .events import make_event

log = logging.getLogger(__name__)

REST = "https://safetrade.com/api/v2/trade/public"
WS = "wss://safe.trade/api/v2/websocket/public"
REST_MARKETS = "https://safetrade.com/api/v2/trade/public/markets"


class SafeTradeAdapter(BaseAdapter):
    exchange = "safetrade"

    def __init__(self, markets_cfg=None, channels=None, raw_q=None, rest_snapshot_s=30.0):
        super().__init__(markets_cfg, channels)
        self.raw_q = raw_q
        self.rest_snapshot_s = rest_snapshot_s
        self._health.update({
            "last_snapshot_mono_ns": 0, "last_change_mono_ns": 0,
            "seq": None, "seq_gaps": 0, "deltas_applied": 0, "invalid": False,
        })

    # ---------- REST (aiohttp, не блокирует loop) ----------
    def _http(self, url, timeout=15):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    async def _http_async(self, url, timeout=15):
        return await asyncio.to_thread(self._http, url, timeout)

    def discover_markets(self) -> list:
        data = self._http(REST_MARKETS)
        out = []
        for m in data:
            out.append({
                "exchange": "safetrade", "symbol": f"{m['base_unit'].upper()}/{m['quote_unit'].upper()}",
                "base": m["base_unit"].upper(), "quote": m["quote_unit"].upper(),
                "native": m["id"], "type": "spot", "status": m.get("state", ""),
                "price_precision": m.get("price_precision"), "min_qty": m.get("min_amount"),
                "amount_precision": m.get("amount_precision"),
            })
        return out

    async def rest_depth(self, native: str, limit: int = 100):
        return await self._http_async(f"{REST}/markets/{native}/depth?limit={limit}")

    # ---------- WS stream ----------
    async def stream(self, symbol: str, sink: asyncio.Queue, run_id: str, boot_id: str) -> None:
        native = symbol.replace("/", "").lower()
        canon = symbol.replace("/", "")
        first_snapshot_done = False
        while True:
            try:
                # 1) REST-снимок ДО подписки (стартовое состояние, provisional)
                snap = await self.rest_depth(native, 100)
                t_utc, t_mono = time.time_ns(), time.monotonic_ns()
                await sink.put(make_event(
                    "safetrade", canon, native, "book_snapshot",
                    recv_utc=t_utc, recv_mono=t_mono,
                    bids=snap.get("bids", []), asks=snap.get("asks", []),
                    run_id=run_id, boot_id=boot_id,
                    quality_flags=["rest_provisional", "rest_snapshot"],
                    payload={"rest": True, "limit": 100},
                ))
                self._health["last_snapshot_mono_ns"] = t_mono
                self._mark_msg()

                # 2) WS с ретраями (Cloudflare флаки)
                ws = await self._connect_ws()
                self._mark_reconnect()
                await ws.send(json.dumps({"event": "subscribe", "streams": [f"{native}.depth"]}))

                # 3) периодический REST-снимок (refresh) параллельно с чтением WS
                refresh_task = asyncio.create_task(self._periodic_snapshot(native, canon, sink, run_id, boot_id))
                try:
                    while True:
                        raw, parsed, t_utc, t_mono = await self._ws_recv_msg(ws)
                        self._push_raw(symbol, raw, t_utc, t_mono)
                        if not isinstance(parsed, dict) or "success" in parsed:
                            continue
                        ev = self._parse_delta(canon, native, parsed, t_utc, t_mono, run_id, boot_id)
                        if ev is not None:
                            if not first_snapshot_done:
                                ev["quality_flags"].append("after_first_snapshot")
                            await sink.put(ev)
                            self._mark_msg()
                            first_snapshot_done = True
                finally:
                    refresh_task.cancel()
                    try:
                        await refresh_task
                    except asyncio.CancelledError:
                        pass
                    await ws.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._mark_error(f"{type(e).__name__}: {str(e)[:100]}")
                self._health["connected"] = False
                log.warning("safetrade %s error: %s", symbol, e)
                await asyncio.sleep(3)

    async def _connect_ws(self):
        """Cloudflare: первый коннект иногда таймаутит — ретраи с Origin."""
        last = None
        for i in range(4):
            try:
                return await self.connect(WS, headers={"Origin": "https://safetrade.com"})
            except Exception as e:
                last = e
                await asyncio.sleep(1.5 + i)
        raise last

    async def _periodic_snapshot(self, native, canon, sink, run_id, boot_id):
        while True:
            await asyncio.sleep(self.rest_snapshot_s)
            try:
                snap = await self.rest_depth(native, 100)
                t_utc, t_mono = time.time_ns(), time.monotonic_ns()
                await sink.put(make_event(
                    "safetrade", canon, native, "book_snapshot",
                    recv_utc=t_utc, recv_mono=t_mono,
                    bids=snap.get("bids", []), asks=snap.get("asks", []),
                    run_id=run_id, boot_id=boot_id,
                    quality_flags=["rest_provisional", "rest_snapshot"],
                    payload={"rest": True, "limit": 100},
                ))
                self._health["last_snapshot_mono_ns"] = t_mono
                self._mark_msg()
            except Exception as e:
                self._mark_error(f"snapshot: {type(e).__name__} {str(e)[:80]}")

    def _parse_delta(self, canon, native, parsed, t_utc, t_mono, run_id, boot_id):
        """WS delta: {"btcusdt.depth": {"asks":[[p,q]], "bids":[...], "sequence":N}}.
        sequence монотонный forward; gap/перекос -> invalid (до нового снапшота)."""
        key = f"{native}.depth"
        d = parsed.get(key)
        if not isinstance(d, dict):
            return None
        seq = d.get("sequence")
        prev = self._health["seq"]
        flags = []
        if seq is not None and prev is not None and seq < prev:
            self._health["seq_gaps"] += 1
            self._health["invalid"] = True
            flags.append("sequence_regression")
        elif seq is not None and prev is not None and seq > prev:
            self._health["deltas_applied"] += 1
        elif seq is not None:
            self._health["deltas_applied"] += 1
        if seq is not None:
            self._health["seq"] = seq
        if self._health["invalid"]:
            flags.append("book_invalid")
        self._health["last_change_mono_ns"] = t_mono
        return make_event(
            "safetrade", canon, native, "book_delta",
            recv_utc=t_utc, recv_mono=t_mono,
            exchange_event_ts=None, sequence=seq,
            bids=d.get("bids", []), asks=d.get("asks", []),
            quality_flags=flags, run_id=run_id, boot_id=boot_id, payload=d,
        )

    def health(self) -> dict:
        h = super().health()
        now = time.monotonic_ns()
        h.update({
            "last_snapshot_age_s": None if not self._health["last_snapshot_mono_ns"]
                                     else (now - self._health["last_snapshot_mono_ns"]) / 1e9,
            "last_change_age_s": None if not self._health["last_change_mono_ns"]
                                    else (now - self._health["last_change_mono_ns"]) / 1e9,
            "seq": self._health["seq"], "seq_gaps": self._health["seq_gaps"],
            "deltas_applied": self._health["deltas_applied"], "book_invalid": self._health["invalid"],
        })
        return h

    def _push_raw(self, symbol, raw, t_utc, t_mono):
        if self.raw_q is not None:
            try:
                self.raw_q.put_nowait({"exchange": "safetrade", "symbol": symbol, "raw": raw,
                                       "recv_utc_ns": t_utc, "recv_mono_ns": t_mono})
            except asyncio.QueueFull:
                pass

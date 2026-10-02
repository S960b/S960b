"""Базовый интерфейс адаптера биржи (ТЗ п.4): discover_markets(), stream(), health().
Ревью: connected сбрасывается при ошибке; health на exchange+symbol; ограниченные errors."""
import abc
import asyncio
import json
import logging
import time
from collections import deque
from typing import Optional

import websockets

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


class AdapterError(Exception):
    pass


class BaseAdapter(abc.ABC):
    exchange = "base"

    def __init__(self, markets_cfg: Optional[list] = None, channels: Optional[dict] = None):
        self.markets_cfg = markets_cfg or []
        self.channels = channels or {"bbo": True, "book": True, "trades": False}
        self._health = {"connected": False, "last_msg_mono_ns": 0, "msgs": 0,
                        "errors": deque(maxlen=20), "reconnects": 0}

    @abc.abstractmethod
    def discover_markets(self) -> list:
        ...

    @abc.abstractmethod
    async def stream(self, symbol: str, sink: asyncio.Queue, run_id: str, boot_id: str) -> None:
        ...

    # ---- health ----
    def health(self) -> dict:
        now = time.monotonic_ns()
        age_s = None if not self._health["last_msg_mono_ns"] else (now - self._health["last_msg_mono_ns"]) / 1e9
        return {
            "exchange": self.exchange,
            "connected": bool(self._health["connected"]),
            "last_msg_age_s": age_s,
            "msgs": self._health["msgs"],
            "errors": list(self._health["errors"])[-10:],
            "reconnects": self._health["reconnects"],
        }

    def _mark_msg(self):
        self._health["last_msg_mono_ns"] = time.monotonic_ns()
        self._health["msgs"] += 1
        self._health["connected"] = True

    def _mark_error(self, msg: str):
        self._health["errors"].append(msg)
        self._health["connected"] = False

    def _mark_reconnect(self):
        self._health["reconnects"] += 1

    # ---- WS helpers (общие; время фиксируется ДО json.loads) ----
    async def _ws_recv_msg(self, ws, timeout: float = 30.0):
        """recv с таймаутом только на сетевой recv; тишина НЕ причина reconnect,
        если ping/pong живы (ревью P0-3: разделять liveness и отсутствие изменений)."""
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        t_utc = time.time_ns()
        t_mono = time.monotonic_ns()
        parsed = json.loads(raw)
        return raw, parsed, t_utc, t_mono

    @staticmethod
    async def connect(uri: str, headers: Optional[dict] = None, open_timeout: float = 15.0):
        kw = dict(open_timeout=open_timeout, user_agent_header=UA, close_timeout=3,
                  ping_interval=20, ping_timeout=15)
        if headers:
            kw["additional_headers"] = headers
        return await websockets.connect(uri, **kw)
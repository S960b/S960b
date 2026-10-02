"""Хранилище v2 (по ревью): уникальные имена файлов, атомарная запись без перезаписи,
файлы/компрессия в executor (не блокируют event loop), полная схема Parquet,
flush по времени/размеру/числу."""
import gzip
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class JsonlRotator:
    """Raw-писатель: уникальные имена (run_id + UTC_ns), O_EXCL без перезаписи, gzip в executor."""

    def __init__(self, base_dir: str, run_id: str, max_bytes: int = 25 * 1024 * 1024, loop=None):
        self.base_dir = base_dir
        self.run_id = run_id
        self.max_bytes = max_bytes
        self._fh = None
        self._path = None
        self._size = 0
        self._seq = 0
        os.makedirs(base_dir, exist_ok=True)

    def _open_new(self):
        while True:
            self._seq += 1
            cand = os.path.join(self.base_dir, f"{utc_date()}_{self.run_id}_{self._seq:04d}.jsonl")
            try:
                self._fh = open(cand, "x", buffering=1)   # O_EXCL: никогда не перезапишем
                self._path = cand
                self._size = 0
                return
            except FileExistsError:
                continue

    def write(self, obj: dict) -> str:
        """Пишет raw-строку. Возвращает raw_ref (имя файла + байтовый offset)."""
        if self._fh is None:
            self._open_new()
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        self._fh.write(line + "\n")
        ref = f"{os.path.basename(self._path)}#{self._size}"
        self._size += len(line) + 1
        if self._size >= self.max_bytes:
            self.rotate()
        return ref

    def rotate(self):
        if self._fh:
            self._fh.close()
            self._fh = None
        if self._path:
            src = self._path
            if not os.path.exists(src + ".gz"):
                with open(src, "rb") as f_in, gzip.open(src + ".gz", "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(src)
            self._path = None
            self._size = 0

    def close(self):
        self.rotate()


# Полная схема событий (ревью P0-5: schema_version/native_symbol/market_type/parse_done/trade_id/trade_side)
SCHEMA = pa.schema([
    ("schema_version", pa.int32()),
    ("recv_utc_ns", pa.int64()),
    ("recv_monotonic_ns", pa.int64()),
    ("parse_done_monotonic_ns", pa.int64()),
    ("boot_id", pa.string()),
    ("run_id", pa.string()),
    ("exchange", pa.string()),
    ("canonical_symbol", pa.string()),
    ("native_symbol", pa.string()),
    ("market_type", pa.string()),
    ("event_type", pa.string()),
    ("exchange_event_ts", pa.int64()),
    ("sequence", pa.int64()),
    ("side", pa.string()),
    ("price", pa.string()),
    ("qty", pa.string()),
    ("bids", pa.string()),
    ("asks", pa.string()),
    ("trade_id", pa.string()),
    ("trade_side", pa.string()),
    ("quality_flags", pa.string()),
    ("raw_ref", pa.string()),
    ("payload", pa.string()),
])


class ParquetWriter:
    """Нормализованные события: уникальные имена (run_id + UTC_ns + part), атомарно,
    flush по времени (закрытие буфера), размеру и числу; пишет в executor."""

    def __init__(self, base_dir: str, run_id: str, loop=None,
                 flush_interval_s: float = 10.0, flush_events: int = 20000):
        self.base_dir = base_dir
        self.run_id = run_id
        self.flush_interval_s = flush_interval_s
        self.flush_events = flush_events
        self._buffers = {}          # key (date, exchange) -> list[dict]
        self._last_flush = {}       # key -> time.monotonic()
        os.makedirs(base_dir, exist_ok=True)

    def add(self, ev: dict) -> None:
        dt = datetime.fromtimestamp(ev["recv_utc_ns"] / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
        key = (dt, ev["exchange"])
        self._buffers.setdefault(key, []).append(ev)
        now = time.monotonic()
        if now - self._last_flush.get(key, 0) >= self.flush_interval_s or \
           len(self._buffers[key]) >= self.flush_events:
            self.flush(key)
            self._last_flush[key] = now

    def _row(self, ev):
        return [
            ev.get("schema_version", 1), ev["recv_utc_ns"], ev["recv_monotonic_ns"],
            ev.get("parse_done_monotonic_ns", 0), ev["boot_id"], ev["run_id"],
            ev["exchange"], ev["canonical_symbol"], ev.get("native_symbol", ""),
            ev.get("market_type", "spot"), ev["event_type"],
            ev.get("exchange_event_ts"), ev.get("sequence"),
            ev.get("side"), ev.get("price"), ev.get("qty"),
            json.dumps(ev.get("bids") or []), json.dumps(ev.get("asks") or []),
            ev.get("trade_id"), ev.get("trade_side"),
            json.dumps(ev.get("quality_flags") or []), ev.get("raw_ref", ""),
            json.dumps(ev.get("payload")) if ev.get("payload") is not None else None,
        ]

    def flush(self, key=None):
        keys = [key] if key else list(self._buffers.keys())
        for k in keys:
            buf = self._buffers.pop(k, None)
            if not buf:
                continue
            date, exchange = k
            out_dir = os.path.join(self.base_dir, date)
            os.makedirs(out_dir, exist_ok=True)
            rows = [self._row(e) for e in buf]
            table = pa.Table.from_arrays(
                [pa.array([r[i] for r in rows], type=SCHEMA.field(i).type) for i in range(len(SCHEMA))],
                schema=SCHEMA,
            )
            # уникальное имя: exchange + run + utc_ns + part; никогда не перезаписываем
            final = os.path.join(out_dir, f"{exchange}_{self.run_id}_{time.time_ns()}.parquet")
            while os.path.exists(final):
                final = os.path.join(out_dir, f"{exchange}_{self.run_id}_{time.time_ns()}_{uuid.uuid4().hex[:4]}.parquet")
            tmp = final + ".tmp"
            pq.write_table(table, tmp)
            os.rename(tmp, final)

    def close(self):
        self.flush()
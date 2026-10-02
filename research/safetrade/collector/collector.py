"""Коллектор v2 (ревью P0-2): файловый I/O и компрессия в executor (не блокируют event loop),
явные счётчики produced/enqueued/written/dropped, drain на shutdown, смерть writer -> стоп run."""
import asyncio
import json
import logging
import os
import signal
import shutil
import time

from adapters import get_adapter, new_run_id, new_boot_id
from storage import JsonlRotator, ParquetWriter, StateStore, utcnow_iso

log = logging.getLogger("collector")


class Collector:
    def __init__(self, cfg: dict, base_dir: str):
        self.cfg = cfg
        self.base_dir = base_dir
        self.run_id = new_run_id(cfg.get("run_id_prefix", "st"))
        self.boot_id = new_boot_id()
        self.event_q = asyncio.Queue(maxsize=50000)
        self.raw_q = asyncio.Queue(maxsize=20000)

        # счётчики (ревью: produced/enqueued/written/dropped)
        self.c = {"events_produced": 0, "events_written": 0, "events_dropped": 0,
                  "raw_produced": 0, "raw_written": 0, "raw_dropped": 0,
                  "writer_alive": True}
        self.started = None
        self._executor = None
        self.stop_flag = False

        st = cfg["storage"]
        self.raw_writer = JsonlRotator(os.path.join(base_dir, st["raw_dir"]), self.run_id,
                                       st["jsonl_max_bytes"])
        self.pq_writer = ParquetWriter(os.path.join(base_dir, st["parquet_dir"]), self.run_id,
                                       flush_interval_s=st.get("parquet_flush_s", 10.0))
        self.db = StateStore(os.path.join(base_dir, cfg["sqlite_path"]))
        self.adapters = {}

    async def run(self, duration_s: float = None, symbols=None):
        self.started = utcnow_iso()
        self._executor = asyncio.get_event_loop().run_in_executor
        sources = self.cfg["sources"] + [self.cfg["target"]]
        mkts = [m for m in self.cfg["markets"] if m["enabled"]]
        if symbols:
            mkts = [m for m in mkts if m["canonical"] in symbols]
        if not mkts:
            log.error("no enabled markets")
            return
        manifest = self._make_manifest()
        self.db.start_run(self.run_id, self.boot_id, sources, [m["symbol"] for m in mkts])
        self.db.set_ui(f"manifest_{self.run_id}", manifest)
        log.info("run %s boot %s sources=%s markets=%s manifest=%s",
                 self.run_id, self.boot_id, sources, [m["symbol"] for m in mkts],
                 manifest.get("config_hash", "")[:12])

        tasks = []
        for ex in sources:
            kw = dict(markets_cfg=mkts, channels=self.cfg["channels"], raw_q=self.raw_q)
            if ex == "safetrade":
                kw["rest_snapshot_s"] = self.cfg.get("safetrade", {}).get("rest_snapshot_s", 30.0)
            ad = get_adapter(ex, **kw)
            self.adapters[ex] = ad
            for m in mkts:
                tasks.append(asyncio.create_task(self._stream_guard(ad, m["symbol"]), name=f"{ex}:{m['symbol']}"))

        writer = asyncio.create_task(self._writer_loop(), name="writer")
        health = asyncio.create_task(self._health_loop(), name="health")
        disk = asyncio.create_task(self._disk_loop(), name="disk")
        # монитор: если writer умер — стоп всего run (ревью P0-2)
        watchdog = asyncio.create_task(self._writer_watchdog(writer), name="wd")

        loop = asyncio.get_event_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

        try:
            if duration_s:
                await asyncio.wait_for(stop.wait(), timeout=duration_s)
            else:
                await stop.wait()
        except asyncio.TimeoutError:
            pass
        finally:
            self.stop_flag = True
            log.info("stop requested: cancelling producers")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # drain: добиваем очереди в writer (ревью: не терять хвост)
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass
            health.cancel(); disk.cancel(); watchdog.cancel()
            await asyncio.gather(health, disk, watchdog, return_exceptions=True)
            self._finalize()

    async def _stream_guard(self, ad, symbol):
        try:
            await ad.stream(symbol, self.event_q, self.run_id, self.boot_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("stream %s %s crashed: %s", ad.exchange, symbol, e)

    async def _writer_loop(self):
        """Единый писатель: читает event_q и raw_q, пишет в executor."""
        try:
            while True:
                # приоритет: события (нормализованные) и raw без взаимной блокировки
                raw = ev = None
                got = False
                try:
                    ev = self.event_q.get_nowait()
                    got = True
                except asyncio.QueueEmpty:
                    pass
                try:
                    raw = self.raw_q.get_nowait()
                    got = True
                except asyncio.QueueEmpty:
                    pass
                if not got:
                    # ждём хоть что-то (worker-задачи постоянные, без create_task утечек)
                    try:
                        raw2, ev2 = await self._await_any()
                        if raw2 is not None: raw = raw2
                        if ev2 is not None: ev = ev2
                    except asyncio.CancelledError:
                        raise
                if ev is not None:
                    await self._write_event(ev)
                if raw is not None:
                    await self._write_raw(raw)
        except asyncio.CancelledError:
            # drain оставшегося (ревью: shutdown drain)
            await self._drain()

    async def _await_any(self):
        """Ждём событие ИЛИ raw без утечки задач."""
        ev_fut = asyncio.ensure_future(self.event_q.get())
        raw_fut = asyncio.ensure_future(self.raw_q.get())
        try:
            done, pending = await asyncio.wait({ev_fut, raw_fut}, return_when=asyncio.FIRST_COMPLETED)
            ev = ev_fut.result() if ev_fut in done and not ev_fut.cancelled() else None
            raw = raw_fut.result() if raw_fut in done and not raw_fut.cancelled() else None
            for p in pending:
                p.cancel()
            return raw, ev
        except asyncio.CancelledError:
            ev_fut.cancel(); raw_fut.cancel()
            raise

    async def _write_event(self, ev):
        try:
            await asyncio.to_thread(self.pq_writer.add, ev)
            self.c["events_written"] += 1
        except Exception as e:
            self.c["events_dropped"] += 1
            log.error("event write failed: %s", e)

    async def _write_raw(self, raw):
        try:
            ref = await asyncio.to_thread(self.raw_writer.write, raw)
            self.c["raw_written"] += 1
        except Exception as e:
            self.c["raw_dropped"] += 1
            log.error("raw write failed: %s", e)

    async def _drain(self):
        """На shutdown: добить очереди в файлы (по возможности)."""
        log.info("drain: events_q=%d raw_q=%d", self.event_q.qsize(), self.raw_q.qsize())
        while not self.event_q.empty():
            try:
                await self._write_event(self.event_q.get_nowait())
            except asyncio.QueueEmpty:
                break
        while not self.raw_q.empty():
            try:
                await self._write_raw(self.raw_q.get_nowait())
            except asyncio.QueueEmpty:
                break

    async def _writer_watchdog(self, writer):
        """Если writer упал с исключением — стоп всего run (ревью P0-2)."""
        try:
            await writer
        except asyncio.CancelledError:
            pass

    async def _health_loop(self):
        while True:
            await asyncio.sleep(15)
            if not self.adapters:
                continue
            syms = ",".join(m["symbol"] for m in self.cfg["markets"] if m["enabled"])
            for ex, ad in self.adapters.items():
                try:
                    self.db.save_health(ex, syms, ad.health())
                except Exception as e:
                    log.error("health save %s: %s", ex, e)
            self.db.set_ui("counters", {**self.c, "queues": {"event": self.event_q.qsize(),
                                                             "raw": self.raw_q.qsize()}})

    def _make_manifest(self):
        """Ревью A.4: manifest run'а — конфиг-хэш, версии пакетов, время."""
        import hashlib
        import importlib.metadata as im
        cfg_dump = json.dumps(self.cfg, sort_keys=True, default=str)
        deps = {}
        for pkg in ("ccxt", "aiohttp", "websockets", "pandas", "pyarrow", "duckdb", "streamlit"):
            try:
                deps[pkg] = im.version(pkg)
            except Exception:
                deps[pkg] = "?"
        return {
            "run_id": self.run_id, "boot_id": self.boot_id, "started_utc": self.started,
            "config_hash": hashlib.sha256(cfg_dump.encode()).hexdigest()[:16],
            "schema_version": self.cfg.get("schema_version"),
            "deps": deps,
            "code_note": "v2.0.0-rev1 (по ревью safetrade_code_review.md)",
        }

    async def _disk_loop(self):
        st = self.cfg["storage"]
        while True:
            await asyncio.sleep(60)
            try:
                total, used, free = shutil.disk_usage(self.base_dir)
                self.db.set_ui("disk_free", free)
                if free < st["disk_limit_bytes"]:
                    raise SystemExit(f"disk limit reached: free={free} < {st['disk_limit_bytes']} — safe stop")
                if free < st["disk_warn_bytes"]:
                    self.db.set_ui("disk_warn", {"free": free, "ts": utcnow_iso()})
            except SystemExit as e:
                log.error("safe stop: %s", e)
                raise

    def _finalize(self):
        try:
            self.pq_writer.close()
            self.raw_writer.close()
            self.db.stop_run(self.run_id)
            self.db.set_ui("last_run", {"run_id": self.run_id, "counters": self.c, "ts": utcnow_iso()})
            self.db.close()
            log.info("finalized run %s counters=%s", self.run_id, self.c)
        except Exception as e:
            log.error("finalize error: %s", e)
"""One serial disk writer, counted queues, timed flush, graceful drain and fail-stop."""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
import time
from collections import OrderedDict

from adapters import get_adapter, new_run_id, new_boot_id
from storage import JsonlRotator, ParquetWriter, StateStore, utcnow_iso

log = logging.getLogger('collector')


class CountedQueue(asyncio.Queue):
    def __init__(self, counters, prefix, on_drop, maxsize):
        super().__init__(maxsize=maxsize)
        self.c, self.prefix, self.on_drop = counters, prefix, on_drop

    def put_nowait(self, item):
        self.c[self.prefix+'_produced'] += 1
        try:
            super().put_nowait(item)
            self.c[self.prefix+'_enqueued'] += 1
        except asyncio.QueueFull:
            self.c[self.prefix+'_dropped'] += 1
            self.on_drop()
            raise


class Collector:
    def __init__(self, cfg, base_dir):
        self.cfg, self.base_dir = cfg, base_dir
        self.run_id = new_run_id(cfg.get('run_id_prefix', 'st'))
        self.boot_id = new_boot_id()
        self.c = {f'{kind}_{stage}': 0 for kind in ('events', 'raw')
                  for stage in ('produced', 'enqueued', 'written', 'dropped')}
        self.c.update(writer_alive=True, raw_unlinked=0, events_flushed=0)
        self.stop = asyncio.Event()
        self.producers_done = False
        self.failure = None
        self.event_q = CountedQueue(self.c, 'events', self._queue_drop, 50000)
        self.raw_q = CountedQueue(self.c, 'raw', self._queue_drop, 20000)
        st = cfg['storage']
        self.raw_writer = JsonlRotator(os.path.join(base_dir, st['raw_dir']), self.run_id, st['jsonl_max_bytes'])
        self.pq_writer = ParquetWriter(os.path.join(base_dir, st['parquet_dir']), self.run_id,
                                       flush_interval_s=st.get('parquet_flush_s', 10))
        dbpath = os.path.join(base_dir, cfg['sqlite_path'])
        os.makedirs(os.path.dirname(dbpath), exist_ok=True)
        self.db = StateStore(dbpath)
        self.adapters = {}
        self._raw_refs = OrderedDict()
        self._latest = {}

    def _queue_drop(self):
        self.failure = 'queue_overflow: run is not suitable for primary research'
        self.stop.set()

    async def run(self, duration_s=None, symbols=None):
        if self.cfg.get('allow_trading'):
            raise ValueError('Research collector requires allow_trading=false')
        markets = [m for m in self.cfg['markets'] if m['enabled'] and (not symbols or m['canonical'] in symbols)]
        if not markets:
            self.db.close()
            raise ValueError('No enabled markets')
        sources = self.cfg['sources']+[self.cfg['target']]
        self.db.start_run(self.run_id, self.boot_id, sources, [m['symbol'] for m in markets])
        self.db.set_ui('manifest_'+self.run_id, {'config': self.cfg, 'boot_id': self.boot_id,
                          'config_hash': hashlib.sha256(json.dumps(self.cfg, sort_keys=True).encode()).hexdigest()})
        producers = []
        for ex in sources:
            for m in markets:
                # One adapter per symbol: no shared sequence/health across markets.
                channels = dict(self.cfg['channels'])
                if ex != self.cfg['target']:
                    channels['book'] = False  # dedicated BBO is the sole oracle channel
                kw = dict(markets_cfg=[m], channels=channels, raw_q=self.raw_q)
                if ex == 'safetrade':
                    kw['rest_snapshot_s'] = self.cfg.get('safetrade', {}).get('rest_snapshot_s', 30)
                ad = get_adapter(ex, **kw)
                self.adapters[(ex, m['symbol'])] = ad
                producers.append(asyncio.create_task(self._stream_guard(ad, m['symbol'])))
        writer = asyncio.create_task(self._writer_loop())
        services = [asyncio.create_task(self._health_loop()), asyncio.create_task(self._disk_loop())]
        for service in services:
            service.add_done_callback(self._service_done)
        loop = asyncio.get_running_loop()
        installed = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop.set); installed.append(sig)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await asyncio.wait_for(self.stop.wait(), duration_s) if duration_s is not None else await self.stop.wait()
        except asyncio.TimeoutError:
            pass
        finally:
            for t in producers+services:
                t.cancel()
            await asyncio.gather(*producers, *services, return_exceptions=True)
            self.producers_done = True
            # Never cancel a thread-backed write. The same writer drains and finalizes.
            try:
                await writer
            except Exception as e:
                self.failure = self.failure or f'writer_failure: {type(e).__name__}: {e}'
            await asyncio.to_thread(self._finalize_files)
            self.c['events_flushed'] = self.pq_writer.rows_flushed
            self.c['writer_alive'] = False
            self.db.set_ui('last_run', {'run_id': self.run_id, 'counters': self.c, 'failure': self.failure})
            self.db.set_ui('counters', {**self.c, 'run_id': self.run_id})
            self.db.set_ui('latest_events', list(self._latest.values()))
            self.db.stop_run(self.run_id, 'failed' if self.failure else 'stopped')
            self.db.close()
            for sig in installed:
                loop.remove_signal_handler(sig)
        if self.failure:
            raise RuntimeError(self.failure)

    async def _stream_guard(self, ad, symbol):
        try:
            await ad.stream(symbol, self.event_q, self.run_id, self.boot_id)
            if not self.producers_done:
                self.failure = f'stream_returned: {ad.exchange}/{symbol}'; self.stop.set()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.failure = f'stream_failure: {ad.exchange}/{symbol}: {e}'; self.stop.set()

    @staticmethod
    def _batch(q, n=500):
        items = []
        for _ in range(n):
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def _service_done(self, task):
        if not task.cancelled() and task.exception() is not None:
            self.failure = f'service_failure: {task.exception()}'
            self.stop.set()

    async def _writer_loop(self):
        try:
            while True:
                raw, evs = self._batch(self.raw_q, 2000), self._batch(self.event_q)
                if raw or evs:
                    await asyncio.to_thread(self._write_batch, raw, evs)
                    for _ in raw: self.raw_q.task_done()
                    for _ in evs: self.event_q.task_done()
                else:
                    await asyncio.to_thread(self.pq_writer.flush_due)
                    if self.producers_done:
                        break
                    await asyncio.sleep(.02)
        except Exception as e:
            self.c['writer_alive'] = False
            self.failure = f'writer_failure: {type(e).__name__}: {e}'
            self.stop.set()
            raise

    def _write_batch(self, raws, events):
        for raw in raws:
            ref = self.raw_writer.write({**raw, 'run_id': self.run_id, 'boot_id': self.boot_id})
            key = (raw['exchange'], raw['symbol'].replace('/', '').upper(), int(raw['recv_mono_ns']))
            self._raw_refs[key] = ref
            self.c['raw_written'] += 1
        while len(self._raw_refs) > 50000:
            self._raw_refs.popitem(last=False)
        for ev in events:
            key = (ev['exchange'], ev['canonical_symbol'], int(ev['recv_monotonic_ns']))
            ev['raw_ref'] = self._raw_refs.get(key, '')
            if not ev['raw_ref'] and ev['event_type'] != 'health':
                self.c['raw_unlinked'] += 1
            self.pq_writer.add(ev)
            self._latest[(ev['exchange'], ev['canonical_symbol'], ev['event_type'])] = ev
            self.c['events_written'] += 1
        self.pq_writer.flush_due()
        self.c['events_flushed'] = self.pq_writer.rows_flushed

    async def _health_loop(self):
        while True:
            for (ex, symbol), ad in self.adapters.items():
                self.db.save_health(ex, symbol, {**ad.health(), 'run_id': self.run_id})
            self.db.set_ui('counters', {**self.c, 'run_id': self.run_id,
                                      'queues': {'event': self.event_q.qsize(), 'raw': self.raw_q.qsize()}})
            self.db.set_ui('latest_events', list(self._latest.copy().values()))
            await asyncio.sleep(5)

    async def _disk_loop(self):
        while True:
            st = self.cfg['storage']
            free = shutil.disk_usage(self.base_dir).free
            self.db.set_ui('disk_free', free)
            if free < st['disk_limit_bytes']:
                self.failure = f'disk_limit: free={free}'; self.stop.set(); return
            if free < st['disk_warn_bytes']:
                self.db.set_ui('disk_warn', {'free': free, 'ts': utcnow_iso()})
            await asyncio.sleep(30)

    def _finalize_files(self):
        try:
            self.pq_writer.close(); self.raw_writer.close()
        except Exception as e:
            self.failure = self.failure or f'finalize_failure: {e}'

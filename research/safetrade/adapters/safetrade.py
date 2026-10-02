"""SafeTrade: independent REST observations and raw WS deltas.

No claimed REST/WS sequence synchronisation. REST runs even while WS is unavailable;
its timestamp is response-receipt, with request time and RTT retained in raw/payload.
"""
import asyncio
import json
import logging
import time
import urllib.request

from .base import BaseAdapter, UA
from .events import make_event

log = logging.getLogger(__name__)
REST = 'https://safetrade.com/api/v2/trade/public'
WS = 'wss://safe.trade/api/v2/websocket/public'
REST_MARKETS = REST+'/markets'


class SafeTradeAdapter(BaseAdapter):
    exchange = 'safetrade'

    def __init__(self, markets_cfg=None, channels=None, raw_q=None, rest_snapshot_s=30.0):
        super().__init__(markets_cfg, channels)
        self.raw_q = raw_q
        self.rest_snapshot_s = rest_snapshot_s
        self._health.update(last_snapshot_mono_ns=0, last_change_mono_ns=0,
                            seq=None, seq_gaps=0, deltas_applied=0, invalid=True,
                            rest_errors=0, rest_snapshots=0)

    def _http(self, url, timeout=15):
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def discover_markets(self):
        return [{'exchange': self.exchange, 'symbol': f"{m['base_unit'].upper()}/{m['quote_unit'].upper()}",
                 'base': m['base_unit'].upper(), 'quote': m['quote_unit'].upper(), 'native': m['id'],
                 'type': 'spot', 'status': m.get('state'), 'price_precision': m.get('price_precision'),
                 'min_qty': m.get('min_amount'), 'amount_precision': m.get('amount_precision')}
                for m in self._http(REST_MARKETS)]

    def _depth_observation(self, native, limit):
        url = f'{REST}/markets/{native}/depth?limit={limit}'
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        request_utc, request_mono = time.time_ns(), time.monotonic_ns()
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode('utf-8')
            recv_utc, recv_mono = time.time_ns(), time.monotonic_ns()
        return raw, json.loads(raw), recv_utc, recv_mono, request_utc, request_mono

    async def rest_depth(self, native, limit=100):
        return await asyncio.to_thread(self._depth_observation, native, limit)

    async def stream(self, symbol, sink, run_id, boot_id):
        native, canon = symbol.replace('/', '').lower(), symbol.replace('/', '')
        rest = asyncio.create_task(self._periodic_snapshot(native, canon, sink, run_id, boot_id))
        try:
            while True:
                ws = None
                try:
                    ws = await self.connect(WS, headers={'Origin': 'https://safetrade.com'})
                    self._mark_reconnect()
                    self._health['seq'] = None
                    await ws.send(json.dumps({'event': 'subscribe', 'streams': [f'{native}.depth']}))
                    while True:
                        raw, parsed, utc, mono = await self._ws_recv_msg(ws)
                        self._push_raw(symbol, raw, utc, mono)
                        ev = self._parse_delta(canon, native, parsed, utc, mono, run_id, boot_id)
                        if ev is not None:
                            await sink.put(ev)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._mark_error(f'{type(e).__name__}: {str(e)[:100]}')
                    log.warning('SafeTrade WS %s: %s', symbol, e)
                    await self._emit_reset(sink, symbol, run_id, boot_id)
                    await asyncio.sleep(3)
                finally:
                    if ws is not None:
                        await ws.close()
        finally:
            rest.cancel()
            await asyncio.gather(rest, return_exceptions=True)

    async def _periodic_snapshot(self, native, canon, sink, run_id, boot_id):
        while True:
            start = time.monotonic()
            try:
                raw, snap, utc, mono, request_utc, request_mono = await self.rest_depth(native)
                rtt = (mono-request_mono)/1e9
                if self.raw_q is not None:
                    self.raw_q.put_nowait({'exchange': self.exchange, 'symbol': canon, 'raw': raw,
                                          'recv_utc_ns': utc, 'recv_mono_ns': mono,
                                          'request_utc_ns': request_utc, 'request_mono_ns': request_mono,
                                          'rtt_s': rtt, 'transport': 'rest'})
                await sink.put(make_event(self.exchange, canon, native, 'book_snapshot',
                               recv_utc=utc, recv_mono=mono, bids=snap.get('bids', []), asks=snap.get('asks', []),
                               run_id=run_id, boot_id=boot_id,
                               quality_flags=['rest_provisional', 'rest_snapshot', 'observation_only'],
                               payload={'rest': True, 'request_utc_ns': request_utc,
                                        'request_mono_ns': request_mono, 'rtt_s': rtt}))
                self._health['last_snapshot_mono_ns'] = mono
                self._health['rest_snapshots'] += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._health['rest_errors'] += 1
                self._health['errors'].append(f'REST: {type(e).__name__}: {str(e)[:80]}')
            await asyncio.sleep(max(.1, self.rest_snapshot_s-(time.monotonic()-start)))

    def _parse_delta(self, canon, native, parsed, t_utc, t_mono, run_id, boot_id):
        d = parsed.get(f'{native}.depth') if isinstance(parsed, dict) else None
        if not isinstance(d, dict):
            return None
        seq, prev = d.get('sequence'), self._health['seq']
        flags = ['unsynchronized_delta', 'observation_only']
        if seq is not None and prev is not None and seq < prev:
            self._health['seq_gaps'] += 1; flags.append('sequence_regression')
        self._health['seq'] = seq
        self._health['last_change_mono_ns'] = t_mono
        return make_event(self.exchange, canon, native, 'book_delta', recv_utc=t_utc, recv_mono=t_mono,
                          sequence=seq, bids=d.get('bids', []), asks=d.get('asks', []),
                          quality_flags=flags, run_id=run_id, boot_id=boot_id, payload=d)

    def health(self):
        h = super().health()
        now = time.monotonic_ns()
        for key in ('last_snapshot', 'last_change'):
            ts = self._health[key+'_mono_ns']
            h[key+'_age_s'] = (now-ts)/1e9 if ts else None
        h.update({key: self._health[key] for key in ('seq', 'seq_gaps', 'deltas_applied', 'rest_errors', 'rest_snapshots')})
        h['book_synchronized'] = False
        return h

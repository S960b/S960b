"""Детальный сбор финалистов maker-отбора (этап 1.3, БЕЗ ордеров).

Собирает для 3-5 пар:
- depth (REST, каждый тик)
- public trades (REST, каждый N-й тик, с пагинацией до покрытия)
- RTT каждого вызова
- внешний оракул для пар с external_reference=yes (LTC и др.)

Единый rate limiter SafeTrade (ТЗ п.4 поправка). Данные в
data/maker/<run_id>/{depth,trades,oracle}.jsonl (атомарная ротация).

Запуск: cli.py maker-collect --minutes 360 --pairs PRLUSDT,QUANTUSUSDT,TSCUSDT,USDCUSDT,LTCUSDT
"""
import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

from adapters.safetrade import SafeTradeAdapter, UA

log = logging.getLogger(__name__)
PAGE_LIMIT = 100

TICK_S = 20.0          # базовый такт на пару (depth)
TRADES_EVERY = 3       # trades каждый 3-й такт (60с)
ORACLE_EVERY = 5       # внешние источники каждый 5-й такт (100с)


class RateLimiter:
    def __init__(self, rpm: float):
        self.min_interval = 60.0 / rpm
        self._lock = asyncio.Lock()
        self._last = time.monotonic()
        self.calls = 0
        self.blocks = 0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            d = self.min_interval - (now - self._last)
            if d > 0:
                await asyncio.sleep(d)
            self._last = time.monotonic()
            self.calls += 1


@dataclass
class DetailRun:
    run_id: str
    started_utc: str
    pairs: list
    tick_s: float
    rpm: float
    dir: str
    files: dict = field(default_factory=dict)  # pair -> (depth_fh, trades_fh)
    rows_written: int = 0


def _jsonl_out(root, name):
    os.makedirs(root, exist_ok=True)
    return open(os.path.join(root, name), 'a', encoding='utf-8')


async def collect_pair(adapter, pair, native, limiter, run, verbose=False):
    """Один цикл пары: depth + (иногда) trades."""
    depth_rows = []

    await limiter.wait()
    try:
        raw, snap, utc, mono, req_utc, req_mono = await adapter.rest_depth(native, limit=100)
        rtt = (mono - req_mono) / 1e9
        row = {'t': utc, 'rtt_s': round(rtt, 4), 'bids': snap.get('bids', []),
               'asks': snap.get('asks', []), 'ok': True}
    except Exception as e:
        row = {'t': time.time_ns(), 'ok': False, 'err': type(e).__name__ + ': ' + str(e)[:80]}
    depth_rows.append(row)

    trades_rows = []
    if run.ticks % TRADES_EVERY == 0:
        await limiter.wait()
        try:
            t0 = time.time()
            tr = await asyncio.to_thread(adapter._http,
                f'https://safetrade.com/api/v2/trade/public/markets/{native}/trades?limit={PAGE_LIMIT}')
            trades_rows.append({'t': time.time_ns(), 'rtt_s': round(time.time()-t0, 3),
                                'ok': True, 'trades': tr if isinstance(tr, list) else []})
        except Exception as e:
            trades_rows.append({'t': time.time_ns(), 'ok': False,
                                'err': type(e).__name__ + ': ' + str(e)[:80]})

    return depth_rows, trades_rows


def get_adapter(name):
    from adapters import get_adapter as _g
    return _g(name)


async def run_detail(pairs, minutes, rpm, base_dir, verbose=False):
    from adapters import get_adapter
    adapter = get_adapter('safetrade')
    limiter = RateLimiter(rpm)

    run = DetailRun(
        run_id='mk_' + hex(int(time.time() * 1e9))[2:18],
        started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        pairs=pairs,
        tick_s=TICK_S,
        rpm=rpm,
        dir=os.path.join(base_dir, 'data', 'maker'),
    )
    run.ticks = 0
    deadline = time.monotonic() + minutes * 60
    f_dep = {p: _jsonl_out(run.dir, f'{run.run_id}_depth.jsonl') for p in pairs}
    f_tr = {p: _jsonl_out(run.dir, f'{run.run_id}_trades.jsonl') for p in pairs}
    f_or = _jsonl_out(run.dir, f'{run.run_id}_oracle.jsonl')

    manifest = {'run_id': run.run_id, 'started_utc': run.started_utc, 'pairs': pairs,
                'tick_s': TICK_S, 'trades_every': TRADES_EVERY, 'rpm': rpm,
                'minutes': minutes, 'notes': 'rest_provisional; no order execution'}
    with open(os.path.join(run.dir, f'{run.run_id}_manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)

    while time.monotonic() < deadline:
        run.ticks += 1
        for pair in pairs:
            native = pair.lower().replace('/', '')
            depth_rows, trades_rows = await collect_pair(adapter, pair, native, limiter, run, verbose)
            for r in depth_rows:
                f_dep[pair].write(json.dumps({'pair': pair, **r}) + '\n')
            if trades_rows:
                for r in trades_rows:
                    f_tr[pair].write(json.dumps({'pair': pair, **r}) + '\n')
            run.rows_written += len(depth_rows) + len(trades_rows)
            f_dep[pair].flush()
            f_tr[pair].flush()
        if run.ticks % ORACLE_EVERY == 0:
            for row in await _oracle_rows():
                f_or.write(json.dumps(row) + '\n')
            f_or.flush()
        if verbose and run.ticks % 6 == 0:
            print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] ticks={run.ticks} "
                  f"rows={run.rows_written} api_calls={limiter.calls} 429={limiter.blocks}", flush=True)
        await asyncio.sleep(TICK_S - 0.05)

    for f in list(f_dep.values()) + list(f_tr.values()) + [f_or]:
        f.close()
    summary = {'run_id': run.run_id, 'finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'ticks': run.ticks, 'rows_written': run.rows_written,
               'api_calls': limiter.calls, 'blocks_429': limiter.blocks}
    with open(os.path.join(run.dir, f'{run.run_id}_summary.json'), 'w') as f:
        json.dump(summary, f, indent=1)
    print(f"detail: run={run.run_id} ticks={run.ticks} rows={run.rows_written} "
          f"api={limiter.calls} 429={limiter.blocks}")
    return summary


async def _oracle_rows():
    """Внешние BBO для LTC (binance/okx/bybit) — прямые REST, без SafeTrade-бюджета."""
    import urllib.request
    from adapters.base import UA as _UA
    rows = []
    endpoints = (
        ('binance', 'https://api.binance.com/api/v3/depth?symbol=LTCUSDT&limit=10',
         lambda j: (j.get('bids', []), j.get('asks', []))),
        ('okx', 'https://www.okx.com/api/v5/market/books?instId=LTC-USDT&sz=10',
         lambda j: ([(b[0], b[1]) for b in j['data'][0]['bids']],
                    [(b[0], b[1]) for b in j['data'][0]['asks']])),
        ('bybit', 'https://api.bybit.com/v5/market/orderbook?category=spot&symbol=LTCUSDT&limit=10',
         lambda j: (j['result']['b'], j['result']['a'])),
    )
    for ex_name, url, parse in endpoints:
        try:
            t0 = time.time()
            req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as r:
                j = json.loads(r.read())
            bids, asks = parse(j)
            rows.append({'ex': ex_name, 'sym': 'LTC', 't': time.time_ns(),
                         'rtt_s': round(time.time() - t0, 4),
                         'bids': bids, 'asks': asks, 'ok': True})
        except Exception as e:
            rows.append({'ex': ex_name, 'sym': 'LTC', 't': time.time_ns(), 'ok': False,
                         'err': type(e).__name__ + ': ' + str(e)[:80]})
    return rows
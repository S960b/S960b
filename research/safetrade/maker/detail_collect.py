"""Детальный сбор финалистов maker-отбора (этап 1.3, БЕЗ ордеров).

Ревью 738de3a (P0.1, P1):
- Сбор trades по страницам ДО подтверждённого перекрытия с прошлым poll
  (или временной границей), под общим rate limiter; сохраняются страницы,
  range ID/time, coverage интервала. Невозможность догнать =>
  coverage_unknown/history_truncated, не «0 trades».
- Первый poll после старта помечается warmup (исторический срез).
- Monotonic deadlines вместо sleep(tick) — фактический cadence близок к
  заявленному и фиксируется в summary (gap/RTT p50/p95, lateness).
- Manifest: schema_version, code commit, параметры, пары, start/end, segment.
- writer: единый handle на run (не по handle на пару), try/finally, атомарный
  summary, graceful finish.

Запуск: cli.py maker-collect --pairs ... --minutes 360 --rpm 20 [--segment N]
"""
import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field

from adapters.safetrade import SafeTradeAdapter

log = logging.getLogger(__name__)
PAGE_LIMIT = 100
TICK_S = 20.0          # целевой такт на пару (depth)
TRADES_EVERY = 3       # trades каждый 3-й такт (60с)
ORACLE_EVERY = 5       # внешние BBO каждые 5-й такт (100с)
MAX_TRADE_PAGES = 40   # предельные страницы на один poll (не тратить весь бюджет)


class RateLimiter:
    def __init__(self, rpm: float):
        self.min_interval = 60.0 / rpm
        self._lock = asyncio.Lock()
        self._last = time.monotonic()
        self.calls = 0
        self.blocks_429 = 0
        self.rtts = []

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            d = self.min_interval - (now - self._last)
            if d > 0:
                await asyncio.sleep(d)
            self._last = time.monotonic()
            self.calls += 1

    def record_rtt(self, s):
        self.rtts.append(s)


@dataclass
class SegmentMeta:
    run_id: str
    segment: int
    started_utc: str
    pairs: list
    params: dict


async def _fetch_trades_detailed(adapter, native, limiter, watermark_time=None):
    """Страницы trades ДО перекрытия с watermark (сделки старше watermark —
    уже видели в прошлом poll) или достижения локального лимита страниц.

    watermark_time — oldest event time прошлого poll (включительно): как только
    страница целиком состоит из сделок <= watermark — поток догнан.

    Возвращает (records, coverage, first_id, last_id, first_ts, last_ts, pages).
    """
    from adapters.safetrade import REST as _REST
    records = []
    seen_ids = set()
    pages_read = 0
    coverage = 'coverage_unknown'
    first_id = last_id = None
    first_ts = last_ts = None
    lo = hi = None
    for page in range(1, MAX_TRADE_PAGES + 1):
        url = f'{_REST}/markets/{native}/trades?limit={PAGE_LIMIT}&page={page}'
        t0 = time.monotonic()
        r = await safe_get(adapter, url, limiter)
        limiter.record_rtt(time.monotonic() - t0)
        if not r['ok']:
            coverage = 'request_failed' if page == 1 else 'history_truncated'
            break
        batch = r['data']
        if not isinstance(batch, list):
            coverage = 'coverage_unknown'
            break
        pages_read += 1
        if not batch:
            coverage = 'full'
            break
        # диапазон ID/времени
        ids = [t.get('id') for t in batch if isinstance(t, dict)]
        ts = [t.get('created_at') for t in batch if isinstance(t, dict)]
        if ids:
            lo = min(ids); hi = max(ids)
        if ts and all(parse_ts(x) is not None for x in ts):
            mi = min(parse_ts(x) for x in ts)
            ma = max(parse_ts(x) for x in ts)
            first_ts = ma if first_ts is None else max(first_ts, ma)
            last_ts = mi if last_ts is None else min(last_ts, mi)
        new = [t for t in batch if isinstance(t, dict) and t.get('id') not in seen_ids]
        if not new:
            coverage = 'full' if pages_read == 1 else 'full_at_page'
            break
        # перекрытие с watermark: страница целиком старше watermark — поток догнан
        if watermark_time is not None:
            all_old = all(parse_ts(t.get('created_at')) is not None and
                          parse_ts(t.get('created_at')) <= watermark_time for t in batch)
            if all_old:
                coverage = 'caught_up'
                break
        for t in new:
            seen_ids.add(t.get('id'))
        records.extend(new)
        if page == MAX_TRADE_PAGES:
            coverage = 'history_truncated'
    return records, coverage, lo, hi, first_ts, last_ts, pages_read


async def safe_get(adapter, url, limiter, attempts=3):
    import urllib.request
    import urllib.error
    from adapters.safetrade import UA as _UA
    last_err = None
    for attempt in range(attempts):
        await limiter.wait()
        req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode('utf-8')
            try:
                return {'ok': True, 'data': json.loads(body)}
            except ValueError as e:
                return {'ok': False, 'error': f'parse_error: {e}'}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                limiter.blocks_429 += 1
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            last_err = f'HTTP {e.code}'
            break
        except Exception as e:
            last_err = f'{type(e).__name__}: {str(e)[:80]}'
            await asyncio.sleep(1.0)
    return {'ok': False, 'error': last_err}


def parse_ts(s):
    from maker.util import parse_iso_utc
    return parse_iso_utc(s)


async def _oracle_rows():
    """Внешние BBO для LTC (binance/okx/bybit) — прямые REST, без SafeTrade-бюджета.
    Строго as-of: возраст данных помечается; это sparse context, не свежая цена."""
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
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as r:
                j = json.loads(r.read())
            bids, asks = parse(j)
            rows.append({'ex': ex_name, 'sym': 'LTC', 't': time.time_ns(),
                         'rtt_s': round(time.time() - t0, 4),
                         'age_s': 0, 'bids': bids, 'asks': asks, 'ok': True})
        except Exception as e:
            rows.append({'ex': ex_name, 'sym': 'LTC', 't': time.time_ns(), 'ok': False,
                         'err': type(e).__name__ + ': ' + str(e)[:80]})
    return rows


async def run_detail(pairs, minutes, rpm, base_dir, verbose=False, segment=1, resume_run_id=None):
    from adapters import get_adapter
    adapter = get_adapter('safetrade')
    limiter = RateLimiter(rpm)

    # resume_run_id: продолжение существующего сегмента (дописывание новым поллингом)
    run_id = resume_run_id or ('mk_' + hex(int(time.time() * 1e9))[2:18])
    run_dir = os.path.join(base_dir, 'data', 'maker')
    os.makedirs(run_dir, exist_ok=True)

    started = time.time()
    started_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started))
    manifest = {
        'schema_version': 2,
        'run_id': run_id, 'segment': segment,
        'started_utc': started_utc,
        'pairs': pairs, 'params': {'tick_s': TICK_S, 'trades_every': TRADES_EVERY,
                                   'oracle_every': ORACLE_EVERY, 'rpm': rpm,
                                   'minutes': minutes, 'max_trade_pages': MAX_TRADE_PAGES},
        'notes': 'rest_provisional; no order execution; '
                 'trades_page_dedup=until_watermark_overlap',
    }
    try:
        manifest['code_commit'] = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ).stdout.strip()[:12]
    except Exception:
        manifest['code_commit'] = 'unknown'
    with open(os.path.join(run_dir, f'{run_id}_manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)

    f_dep = open(os.path.join(run_dir, f'{run_id}_depth.jsonl'), 'a', encoding='utf-8')
    f_tr = open(os.path.join(run_dir, f'{run_id}_trades.jsonl'), 'a', encoding='utf-8')
    f_or = open(os.path.join(run_dir, f'{run_id}_oracle.jsonl'), 'a', encoding='utf-8')

    # watermark по времени прошлого poll (для trades) — None в начале сегмента
    watermark = {}
    rows_written = 0
    ticks = 0
    last_poll = {}          # pair -> dict(последний poll: время, ids)
    deadline = time.monotonic() + minutes * 60
    tick_dur = []
    try:
        while time.monotonic() < deadline:
            tick_start = time.monotonic()
            ticks += 1
            for pair in pairs:
                native = pair.lower().replace('/', '')
                # depth (каждый тик)
                t0 = time.monotonic()
                try:
                    raw, snap, utc, mono, req_utc, req_mono = await adapter.rest_depth(native, limit=100)
                    # limiter для depth тоже учитываем (rate limiter внутри collect_pair вынесен сюда)
                    limiter.record_rtt(time.monotonic() - t0)
                    row = {'pair': pair, 't': utc, 'rtt_s': round((mono - req_mono) / 1e9, 4),
                           'bids': snap.get('bids', []), 'asks': snap.get('asks', []), 'ok': True}
                    f_dep.write(json.dumps(row) + '\n')
                    rows_written += 1
                except Exception as e:
                    f_dep.write(json.dumps({'pair': pair, 't': time.time_ns(), 'ok': False,
                                            'err': type(e).__name__ + ': ' + str(e)[:80]}) + '\n')
                    rows_written += 1
                # trades (каждый 3-й тик): пагинация до перекрытия с прошлым poll
                if ticks % TRADES_EVERY == 0:
                    wm = watermark.get(pair)   # oldest ts прошлого poll
                    recs, cov, lo, hi, fts, lts, pages = await _fetch_trades_detailed(
                        adapter, native, limiter, watermark_time=wm)
                    row = {'pair': pair, 't': time.time_ns(), 'ok': True,
                           'warmup': wm is None, 'coverage': cov,
                           'pages': pages, 'id_range': [lo, hi],
                           'time_range': [fts, lts],
                           'n_records': len(recs), 'trades': recs}
                    f_tr.write(json.dumps(row) + '\n')
                    rows_written += 1
                    if recs and lts is not None:
                        watermark[pair] = lts   # старейшая полученная сделка
                    last_poll[pair] = {'t': time.time_ns(), 'coverage': cov,
                                       'n_records': len(recs)}
            # oracle
            if ticks % ORACLE_EVERY == 0:
                for row in await _oracle_rows():
                    f_or.write(json.dumps(row) + '\n')
                    rows_written += 1
            f_dep.flush(); f_tr.flush(); f_or.flush()
            tick_dur.append(time.monotonic() - tick_start)
            if verbose and ticks % 6 == 0:
                print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] ticks={ticks} "
                      f"rows={rows_written} api={limiter.calls} 429={limiter.blocks_429}", flush=True)
            # monotonic deadline: ждём ДО следующего тика, а не спим фиксированно
            next_tick = tick_start + TICK_S
            wait = next_tick - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
    finally:
        f_dep.close(); f_tr.close(); f_or.close()
        finished_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        gaps = sorted(tick_dur) if tick_dur else []
        summary = {
            'schema_version': 2, 'run_id': run_id, 'segment': segment,
            'started_utc': started_utc, 'finished_utc': finished_utc,
            'elapsed_s': round(time.time() - started, 1),
            'ticks': ticks, 'rows_written': rows_written,
            'api_calls': limiter.calls, 'blocks_429': limiter.blocks_429,
            'cadence': {'tick_median_s': round(gaps[len(gaps)//2], 2) if gaps else None,
                        'tick_p95_s': round(gaps[int(len(gaps)*.95)], 2) if gaps else None,
                        'tick_max_s': round(gaps[-1], 2) if gaps else None,
                        'note': 'wall-clock между begin тиков (вкл. обработку пары)'},
            'trade_poll_state': last_poll,
            'notes': 'rest_provisional; no order execution',
        }
        with open(os.path.join(run_dir, f'{run_id}_summary.json'), 'w') as f:
            json.dump(summary, f, indent=1)
        print(f"detail: run={run_id} seg={segment} ticks={ticks} rows={rows_written} "
              f"api={limiter.calls} 429={limiter.blocks_429}")
        return summary
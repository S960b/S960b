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
MAX_TRADE_PAGES = 40   # абсолютный потолок страниц на один poll (защита)
WARMUP_PAGES = 6       # первый (warmup) poll: 600 последних сделок — достаточно
LIVE_PAGES = 3         # live poll: до границы прошлого полла (обычно 1-2 страницы)
CHECKPOINT_EVERY = 30  # атомарный checkpoint каждые 30 тиков (~10 мин)


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


async def _fetch_trades_detailed(adapter, native, limiter, watermark_time=None, max_pages=LIVE_PAGES):
    """Страницы trades ДО подтверждённого перекрытия с прошлым poll.

    watermark_time — САМОЕ НОВОЕ событие, подтверждённое прошлым poll'ом
    (граница уже покрытого интервала). Как только страница целиком состоит из
    записей строго СТАРШЕ watermark (earliest_ts < watermark) — поток догнан.
    Записи с РАВНЫМ временем НЕ считаются покрытыми (новые ID с тем же ts
    сохраняются; повторы потом убирает дедуп).

    max_pages: лимит страниц НА POLL (warmup 6 / live 3) — чтобы догрузка
    истории одной пары не блокировала стаканы остальных (P0.1 0b71d28).

    Возвращает (records, coverage, lo, hi, first_ts, last_ts, pages).
    coverage: caught_up / full / full_at_page / pagination_not_advancing /
              history_truncated / request_failed / coverage_unknown.
    """
    from adapters.safetrade import REST as _REST
    records = []
    seen_ids = set()
    pages_read = 0
    coverage = 'coverage_unknown'
    first_id = last_id = None
    first_ts = last_ts = None
    for page in range(1, max_pages + 1):
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
            coverage = 'full' if pages_read == 1 else 'full_at_page'
            break
        ids = [t.get('id') for t in batch if isinstance(t, dict)]
        ts = [parse_ts(t.get('created_at')) for t in batch if isinstance(t, dict)]
        if ids:
            first_id = first_id or min(ids)          # границы ВСЕГО сохранённого набора
            last_id = max(last_id or 0, max(ids))
        good_ts = [x for x in ts if x is not None]
        if good_ts:
            # first_ts = НОВЕЙШЕЕ (front), last_ts = СТАРЕЙШЕЕ (tail) — контракт
            # watermark: следующая граница = самый свежий подтверждённый фронт
            if first_ts is None or max(good_ts) > first_ts:
                first_ts = max(good_ts)
            if last_ts is None or min(good_ts) < last_ts:
                last_ts = min(good_ts)
        new = [t for t in batch if isinstance(t, dict) and t.get('id') not in seen_ids]
        if not new:
            # вся страница уже видна: повтор страницы != конец истории
            coverage = 'pagination_not_advancing'
            break
        # пересечение с watermark: страница целиком СТРОГО старше watermark
        if watermark_time is not None and all(
                parse_ts(t.get('created_at')) is not None and
                parse_ts(t.get('created_at')) < watermark_time for t in batch):
            coverage = 'caught_up'
            break
        for t in new:
            seen_ids.add(t.get('id'))
        records.extend(new)
        if page == max_pages:
            coverage = 'history_truncated'
    return records, coverage, first_id, last_id, first_ts, last_ts, pages_read


async def safe_get(adapter, url, limiter, attempts=3):
    import urllib.request
    import urllib.error
    from adapters.safetrade import UA as _UA
    last_err = None
    for attempt in range(attempts):
        await limiter.wait()
        req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})

        def _open():
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode('utf-8')

        try:
            # блокирующий I/O ВНЕ event loop (92d4079: не обещать async-параллельность)
            body = await asyncio.to_thread(_open)
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


def _write_checkpoint(run_dir, run_id, segment, started_utc, ticks, rows_written,
                      limiter, tick_dur, last_poll, status):
    """Атомарный периодический checkpoint (P0.3 0b71d28): heartbeat + прогресс."""
    gaps = sorted(tick_dur) if tick_dur else []
    ck = {
        'schema_version': 2, 'run_id': run_id, 'segment': segment,
        'started_utc': started_utc, 'checkpoint_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'status': status,
        'ticks': ticks, 'rows_written': rows_written,
        'api_calls': limiter.calls, 'blocks_429': limiter.blocks_429,
        'cadence_median_s': round(gaps[len(gaps)//2], 2) if gaps else None,
        'trade_poll_state': last_poll,
    }
    tmp = os.path.join(run_dir, f'{run_id}_checkpoint.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(ck, f, indent=1)
    os.replace(tmp, os.path.join(run_dir, f'{run_id}_checkpoint.json'))


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

    # resume_run_id ЗАПРЕЩЁН (0b71d28/92d4079): дописывание старых JSONL
    # переписывает manifest и сбрасывает watermark. Отказ ДО любого I/O.
    if resume_run_id:
        raise ValueError(
            'resume_run_id отключён: дописывание старых JSONL ломает целостность. '
            'Используйте новый segment/run_id.')

    adapter = get_adapter('safetrade')
    limiter = RateLimiter(rpm)

    run_id = ('mk_' + hex(int(time.time() * 1e9))[2:18])
    run_dir = os.path.join(base_dir, 'data', 'maker')
    os.makedirs(run_dir, exist_ok=True)

    started = time.time()
    started_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started))
    manifest = {
        'schema_version': 2,
        'run_id': run_id, 'segment': segment,
        'started_utc': started_utc,
        'started_epoch': started,
        'pairs': pairs, 'params': {'tick_s': TICK_S, 'trades_every': TRADES_EVERY,
                                   'oracle_every': ORACLE_EVERY, 'rpm': rpm,
                                   'minutes': minutes,
                                   'warmup_pages': WARMUP_PAGES, 'live_pages': LIVE_PAGES},
        'notes': 'rest_provisional; no order execution; '
                 'confirmed_boundary_advances_only_on_full_poll; '
                 'target_boundary=run_start',
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

    # per-pair cadence: фактические промежутки depth/trades (P1 0b71d28)
    pair_times = {pair: {'depth': [], 'trades': []} for pair in pairs}
    last_pair_ts = {pair: {'depth': None, 'trades': None} for pair in pairs}
    waiting = {pair: 0 for pair in pairs}   # суммарное ожидание лимитера (очередь)
    last_depth_ok = {}
    last_trades_ok = {}

    # ПОДТВЕРЖДЁННАЯ граница покрытия (92d4079 P0): продвигается ТОЛЬКО при
    # полном poll (full/full_at_page/caught_up). При truncated/failed/unknown/
    # pagination_not_advancing НЕ двигается — следующий poll дочитывает с неё.
    # Начальная целевая граница = время СТАРТА run (не вся история листинга):
    # первый poll догоняет начало наблюдения и останавливается.
    boundary = {pair: started for pair in pairs}
    first_poll = {pair: True for pair in pairs}   # warmup = первый poll на пару
    rows_written = 0
    ticks = 0
    last_poll = {}
    deadline = time.monotonic() + minutes * 60
    tick_dur = []
    lifecycle = {'status': 'running'}
    CONFIRMED_OK = ('full', 'full_at_page', 'caught_up')
    try:
        while time.monotonic() < deadline:
            tick_start = time.monotonic()
            ticks += 1
            for pair in pairs:
                native = pair.lower().replace('/', '')
                # depth (каждый тик): ОБЯЗАТЕЛЬНО через общий лимитер (P0.2 0b71d28)
                t0 = time.monotonic()
                await limiter.wait()
                w0 = time.monotonic()
                waiting[pair] += (w0 - t0)
                try:
                    raw, snap, utc, mono, req_utc, req_mono = await adapter.rest_depth(native, limit=100)
                    limiter.record_rtt(time.monotonic() - w0)
                    row = {'pair': pair, 't': utc, 'rtt_s': round((mono - req_mono) / 1e9, 4),
                           'bids': snap.get('bids', []), 'asks': snap.get('asks', []), 'ok': True}
                    last_depth_ok[pair] = time.time()
                except Exception as e:
                    if getattr(e, 'code', None) == 429:
                        limiter.blocks_429 += 1
                    row = {'pair': pair, 't': time.time_ns(), 'ok': False,
                           'err': type(e).__name__ + ': ' + str(e)[:80]}
                f_dep.write(json.dumps(row) + '\n')
                rows_written += 1
                # per-pair depth gap (между началами успешных запросов)
                if last_pair_ts[pair]['depth'] is not None:
                    pair_times[pair]['depth'].append(time.monotonic() - last_pair_ts[pair]['depth'])
                last_pair_ts[pair]['depth'] = time.monotonic()
                # trades: live poll до подтверждённой границы; warmup = первый
                if ticks % TRADES_EVERY == 0:
                    wm = boundary.get(pair)          # ПОДТВЕРЖДЁННАЯ граница
                    is_warm = first_poll.get(pair)
                    mp = WARMUP_PAGES if is_warm else LIVE_PAGES
                    recs, cov, lo, hi, fts, lts, pages = await _fetch_trades_detailed(
                        adapter, native, limiter, watermark_time=wm, max_pages=mp)
                    ok_row = cov not in ('request_failed', 'coverage_unknown')
                    bnd_after = boundary.get(pair)
                    if cov in CONFIRMED_OK and fts is not None:
                        # подтверждённая граница продвигается только ВПЕРЁД:
                        # если новых сделок нет, загрузчик вернёт время старой
                        # сделки (fts < wm); max(wm, fts) не позволяет границе
                        # сдвинуться назад и вызвать лишнее перечитывание истории.
                        boundary[pair] = max(wm, fts)
                        bnd_after = boundary[pair]
                    row = {'pair': pair, 't': time.time_ns(), 'ok': ok_row,
                           'warmup': is_warm, 'coverage': cov,
                           'pages': pages, 'id_range': [lo, hi],
                           'time_range': [fts, lts],
                           'boundary_before': wm, 'boundary_after': bnd_after,
                           'n_records': len(recs), 'trades': recs}
                    f_tr.write(json.dumps(row) + '\n')
                    rows_written += 1
                    last_poll[pair] = {'t': time.time_ns(), 'coverage': cov,
                                       'n_records': len(recs)}
                    if ok_row:
                        last_trades_ok[pair] = time.time()   # только успешный ответ
                    first_poll[pair] = False
                    if last_pair_ts[pair]['trades'] is not None:
                        pair_times[pair]['trades'].append(time.monotonic() - last_pair_ts[pair]['trades'])
                    last_pair_ts[pair]['trades'] = time.monotonic()
            # oracle
            if ticks % ORACLE_EVERY == 0:
                for row in await _oracle_rows():
                    f_or.write(json.dumps(row) + '\n')
                    rows_written += 1
            f_dep.flush(); f_tr.flush(); f_or.flush()
            tick_dur.append(time.monotonic() - tick_start)
            # периодический атомарный checkpoint (P0.3 0b71d28)
            if ticks % CHECKPOINT_EVERY == 0:
                _write_checkpoint(run_dir, run_id, segment, started_utc, ticks, rows_written,
                                  limiter, tick_dur, last_poll, 'running')
            if verbose and ticks % 6 == 0:
                print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] ticks={ticks} "
                      f"rows={rows_written} api={limiter.calls} 429={limiter.blocks_429}", flush=True)
            # monotonic deadline: ждём ДО следующего тика, а не спим фиксированно
            next_tick = tick_start + TICK_S
            wait = next_tick - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        lifecycle['status'] = 'completed'
    except asyncio.CancelledError:
        lifecycle['status'] = 'cancelled'
        raise
    except Exception as e:
        lifecycle['status'] = 'failed'
        lifecycle['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        raise
    finally:
        f_dep.close(); f_tr.close(); f_or.close()
        # атомарный summary: tmp + os.replace (P0.3/P1 0b71d28)
        gaps = sorted(tick_dur) if tick_dur else []
        per_pair = {}
        for pair in pairs:
            dd = sorted(pair_times[pair]['depth'])
            tt = sorted(pair_times[pair]['trades'])
            def _q(xs, q):
                if not xs:
                    return None
                return round(xs[min(len(xs)-1, int(len(xs)*q))], 2)
            per_pair[pair] = {
                'depth_gap_s': {'median': _q(dd, .5), 'p95': _q(dd, .95), 'max': _q(dd, 1.0), 'n': len(dd)},
                'trades_gap_s': {'median': _q(tt, .5), 'p95': _q(tt, .95), 'max': _q(tt, 1.0), 'n': len(tt)},
                'depth_age_s': round(time.time() - last_depth_ok[pair], 1) if pair in last_depth_ok else None,
                'trades_age_s': round(time.time() - last_trades_ok[pair], 1) if pair in last_trades_ok else None,
                'limiter_wait_s': round(waiting[pair], 1),
            }
        summary = {
            'schema_version': 2, 'run_id': run_id, 'segment': segment,
            'started_utc': started_utc, 'finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'elapsed_s': round(time.time() - started, 1),
            'status': lifecycle.get('status'),
            **({'error': lifecycle['error']} if 'error' in lifecycle else {}),
            'ticks': ticks, 'rows_written': rows_written,
            'api_calls': limiter.calls, 'blocks_429': limiter.blocks_429,
            'cadence': {'tick_median_s': round(gaps[len(gaps)//2], 2) if gaps else None,
                        'tick_p95_s': round(gaps[int(len(gaps)*.95)], 2) if gaps else None,
                        'tick_max_s': round(gaps[-1], 2) if gaps else None,
                        'note': 'wall-clock длительность обработки тика (вкл. сеть)'},
            'per_pair': per_pair,
            'trade_poll_state': last_poll,
            'confirmed_boundary': boundary,
            'notes': 'rest_provisional; no order execution',
        }
        summary_tmp = os.path.join(run_dir, f'{run_id}_summary.json.tmp')
        with open(summary_tmp, 'w') as f:
            json.dump(summary, f, indent=1)
        os.replace(summary_tmp, os.path.join(run_dir, f'{run_id}_summary.json'))
        print(f"detail: run={run_id} seg={segment} status={lifecycle.get('status')} ticks={ticks} "
              f"rows={rows_written} api={limiter.calls} 429={limiter.blocks_429}")
    return summary
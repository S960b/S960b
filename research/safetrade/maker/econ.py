"""Экономический анализ maker-сбора (этап 1.4+, БЕЗ ордеров).

Ревью d891422 исправлено:
1. Полный оборот бюджета = полный ВХОД + полный ВЫХОД тем же количеством.
   Частичная покупка НЕ выдаётся за полный бюджет; допуск 99.99% убран —
   сравнивается проданное количество с купленным (остаток остаётся).
2. Окно по умолчанию = конец доступных данных (последний валидный снимок/
   poll), НЕ start+1ч. Скорость = по точной длительности окна.
3. Отсутствие будущего снимка = censored (n_unobserved), НЕ «цена не
   изменилась». Статистика только по наблюдаемым случаям; допуск запаздывания.
4. touch_after — ДИАГНОСТИКА пересечения фиксированной цены, не экономический
   вердикт (maker: сторона/цена/время и срок каждой котировки неизвестны).
5. Счётчики качества: повреждённые строки, неразобранные timestamps,
   неизвестная сторона, покрытие интервала, неполные poll'ы.
"""
import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.book import OrderBook
from maker.util import parse_iso_utc, valid_book

NONFULL_POLL = ('request_failed', 'history_truncated', 'coverage_unknown',
                'pagination_not_advancing')
MAX_DELAY_FRAC = 0.25     # допустимое запаздывание будущего снимка: 25% горизонта


def _read_jsonl(path):
    rows, bad = [], 0
    if not os.path.exists(path):
        return rows, 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    return rows, bad


def _entry_quote(bids, asks, budget):
    """Вход: ask-VWAP на бюджет. Возвращает (price, qty, cov) где cov:
    full (весь бюджет потрачен) / partial / none."""
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade', 'canonical_symbol': 'x',
              'bids': bids, 'asks': asks, 'quality_flags': [], 'recv_monotonic_ns': 1})
    price, qty = ob.vwap_cost('ask', Decimal(str(budget)))
    if price is None:
        return None, 0.0, 'none'
    cost = float(qty) * float(price)
    tol = max(1e-6, float(budget) * 1e-9)
    if cost >= float(budget) - tol:
        return float(price), float(qty), 'full'
    return float(price), float(qty), 'partial'


def _sell_qty(bids, qty):
    """Максимально продаваемое количество по bid-глубине (для выхода)."""
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade', 'canonical_symbol': 'x',
              'bids': bids, 'asks': [], 'quality_flags': [], 'recv_monotonic_ns': 1})
    filled = 0.0
    for px, q in sorted(bids, key=lambda x: -float(x[0])):
        need = float(qty) - filled
        if need <= 0:
            break
        filled += min(need, float(q))
    return filled


def econ_report(run_dir, run_id, cutoff_ns=None, budgets=(5, 10, 25),
                horizons=(10, 30, 60, 300)):
    """Экономический отчёт по run. Возвращает dict с агрегатами по парам."""
    depth, bad_d = _read_jsonl(os.path.join(run_dir, f'{run_id}_depth.jsonl'))
    tr_rows, bad_t = _read_jsonl(os.path.join(run_dir, f'{run_id}_trades.jsonl'))
    or_rows, bad_o = _read_jsonl(os.path.join(run_dir, f'{run_id}_oracle.jsonl'))
    manifest = {}
    mp = os.path.join(run_dir, f'{run_id}_manifest.json')
    if os.path.exists(mp):
        manifest = json.load(open(mp))
    # started_epoch ТОЧНЕЕ ISO (секундная точность) — приоритет ему (4a3dc86 п.4)
    started_ts = float(manifest.get('started_epoch') or 0.0)
    if started_ts <= 0:
        started_ts = parse_iso_utc(manifest.get('started_utc')) or 0.0
    if started_ts <= 0:
        raise ValueError(f'econ: manifest {run_id} без started_epoch/started_utc')

    # cutoff: явный ИЛИ конец доступных данных (последний валидный снимок/poll)
    if cutoff_ns is not None:
        cutoff_ns = int(cutoff_ns)
        cutoff_ts = float(cutoff_ns) / 1e9
    else:
        last = started_ts
        for r in reversed(depth):
            if isinstance(r, dict) and r.get('t'):
                last = max(last, float(r['t']) / 1e9)
                break
        for r in reversed(tr_rows):
            if isinstance(r, dict) and r.get('t'):
                last = max(last, float(r['t']) / 1e9)
                break
        cutoff_ts = last
        cutoff_ns = int(round(cutoff_ts * 1e9))
    if cutoff_ts <= started_ts:
        raise ValueError(f'econ: cutoff {cutoff_ts} <= start {started_ts} для {run_id}')
    # длительность окна: от ТОЧНОГО started_epoch (не усечённого ISO),
    # арифметика в целых микросекундах через Decimal — float-шум ~1e-7с
    # из cutoff_ns и секундная точность ISO не должны искажать окно
    # (4a3dc86 п.4/п.5: 6h-срез обязан быть ровно 6.000000ч).
    _started_us = int((Decimal(str(started_ts)) * 1_000_000).to_integral_value())
    _cutoff_us = int((Decimal(cutoff_ns) / 1_000).to_integral_value())
    window_us = Decimal(_cutoff_us) - Decimal(_started_us)
    window_h_exact = float(window_us / Decimal(3.6e9))
    window_h = round(window_h_exact, 4)

    out = {
        'run_id': run_id, 'started_utc': manifest.get('started_utc'),
        'started_epoch': started_ts,
        'cutoff_ns': cutoff_ns,   # исходное целое НАНОСЕКУНД (не float-пересчёт!)
        'cutoff_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(cutoff_ts)),
        'window_h': round(window_h_exact, 4),
        'window_h_exact': window_h_exact,
        'window_sources': 'cutoff=явный' if cutoff_ns is not None else 'cutoff=конец данных',
        'fee_status': 'confirmed_from_api',
                'fee_source': 'GET /api/v2/trade/public/trading_fees (2026-10-04): '
                              'maker=0.001 taker=0.001, market_id=any, group=any',
                'fee_maker_bps': 10.0, 'fee_taker_bps': 10.0,
                'note': ('комиссии подтверждены API trading_fees: maker=0.1% taker=0.1% '
                         '(одинаковые — maker-скидки НЕТ); касание котировки = opportunity, '
                         'НЕ fill; touch_after — диагностика, не вердикт'),
        'pairs': [],
    }

    # --- depth: валидные снимки по парам
    depth_by = defaultdict(list)
    for r in depth:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        rcv = (r.get('t') or 0) / 1e9
        if rcv > cutoff_ts:
            continue
        b, a = r.get('bids'), r.get('asks')
        if b is None or a is None:
            continue
        ok, bb, ba = valid_book(b, a)
        if ok:
            depth_by[r['pair']].append((rcv, b, a))

    # --- trades: уникальные в окне + статусы poll
    trades_by = defaultdict(list)
    poll_status = defaultdict(list)
    unparsed = defaultdict(int)
    unknown_side = defaultdict(int)
    seen = set()
    for r in tr_rows:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        rcv = (r.get('t') or 0) / 1e9
        if rcv > cutoff_ts:
            continue
        pc = r.get('coverage') or 'missing'
        p_ok = r.get('ok', True)
        p_warm = bool(r.get('warmup', False))
        poll_status[r['pair']].append((p_ok, pc, p_warm))
        for t in r.get('trades', []):
            if not isinstance(t, dict) or 'id' not in t:
                continue
            key = (r['pair'], t['id'])
            if key in seen:
                continue
            seen.add(key)
            ts = parse_iso_utc(t.get('created_at'))
            if ts is None:
                unparsed[r['pair']] += 1
                continue
            if not (started_ts <= ts <= cutoff_ts):
                continue
            if not t.get('side'):
                unknown_side[r['pair']] += 1
            trades_by[r['pair']].append((ts, t))

    for pair in sorted(set(list(depth_by) + list(trades_by))):
        o = {'symbol': pair}

        # --- качество данных (d891422 п.5)
        o['unparsed_ts'] = unparsed.get(pair, 0)
        o['unknown_side'] = unknown_side.get(pair, 0)
        st = poll_status.get(pair, [])
        o['poll_n'] = len(st)
        o['poll_nonfull'] = sum(1 for ok, pc, w in st if not ok or pc in NONFULL_POLL)
        o['poll_warmup'] = sum(1 for ok, pc, w in st if w)
        live = [pc for ok, pc, w in st if not w]
        if st and all(ok and pc in ('full', 'full_at_page', 'caught_up') for ok, pc, w in st if not w) \
                and any(not w for ok, pc, w in st):
            o['interval_coverage'] = 'covered'
        elif any(not ok for ok, pc, w in st):
            o['interval_coverage'] = 'request_failed'
        elif any(pc in NONFULL_POLL for ok, pc, w in st if not w):
            o['interval_coverage'] = 'incomplete'
        else:
            o['interval_coverage'] = 'unknown'

        # --- 1/2. оборот и направление (уникальные сделки окна)
        tr = trades_by.get(pair, [])
        o['n_trades'] = len(tr)
        totals = [float(t.get('total') or 0) for _, t in tr if t.get('total') is not None]
        o['notional_usdt'] = round(sum(totals), 2)
        o['notional_per_hour'] = round(sum(totals) / window_h_exact, 2) if totals else 0.0
        sides = [t.get('side') for _, t in tr]
        o['n_buy'] = sides.count('buy')
        o['n_sell'] = sides.count('sell')
        o['buy_fraction'] = round(sides.count('buy') / len(sides), 3) if sides else None
        # оборот по сторонам (доля денег, не доля сделок) + ПРОВЕРКА total vs price*amount
        notional_buy = notional_sell = notional_unknown = 0.0
        o['total_mismatch'] = 0
        o['total_mismatch_rel'] = []
        for _, t in tr:
            try:
                tot = float(t.get('total') or 0)
                px = float(t.get('price') or 0)
                amt = float(t.get('amount') or 0)
            except (TypeError, ValueError):
                o['total_mismatch'] += 1
                continue
            if px and amt:
                calc = px * amt
                rel = abs(tot - calc) / calc if calc else 0.0
                if rel > 1e-6:
                    o['total_mismatch'] += 1
                    o['total_mismatch_rel'].append(round(rel, 4))
            sd = t.get('side')
            if sd == 'buy':
                notional_buy += tot
            elif sd == 'sell':
                notional_sell += tot
            else:
                notional_unknown += tot
        o['notional_buy_usdt'] = round(notional_buy, 2)
        o['notional_sell_usdt'] = round(notional_sell, 2)
        o['notional_unknown_usdt'] = round(notional_unknown, 2)
        if totals:
            s = sorted(totals)
            o['notional_min'] = round(s[0], 4)
            o['notional_p50'] = round(s[len(s)//2], 4)
            o['notional_p90'] = round(s[int(len(s)*.9)], 4)
            o['notional_max'] = round(s[-1], 4)

        # --- 4. возможности по глубине: ВХОД полного бюджета И выход тем же qty
        snaps = depth_by.get(pair, [])
        o['n_depth_snaps'] = len(snaps)
        o['roundtrip_by_budget'] = {}
        o['entry_by_budget'] = {}
        # exit_by_budget: полный оборот бюджета = полный ВХОД и выход тем же qty
        o['exit_by_budget'] = {}
        for b_usdt in budgets:
            full_entry = full_rt = 0
            for _, b, a in snaps:
                ask_px, buy_qty, cov = _entry_quote(b, a, b_usdt)
                o_entry = {'entry_cov': cov}
                if cov == 'full' and ask_px is not None:
                    full_entry += 1
                    sold = _sell_qty(b, buy_qty)
                    # точное сравнение: остаток остаётся, допуск только на погрешность
                    remain = buy_qty - sold
                    if remain <= max(buy_qty * 1e-9, 1e-12):
                        full_rt += 1
            o['entry_by_budget'][str(b_usdt)] = {
                'full_entry_pct': round(100.0 * full_entry / len(snaps), 1) if snaps else None,
                'n_full_entry': full_entry, 'n_snaps': len(snaps),
            }
            o['roundtrip_by_budget'][str(b_usdt)] = {
                'full_rt_pct': round(100.0 * full_rt / len(snaps), 1) if snaps else None,
                'n_full_rt': full_rt, 'n_snaps': len(snaps),
            }
            o['exit_by_budget'][str(b_usdt)] = {
                'full_exit_pct': round(100.0 * full_rt / len(snaps), 1) if snaps else None,
                'n_full': full_rt, 'n_snaps': len(snaps),
                'note': 'полный оборот бюджета: полный вход И выход тем же qty',
            }

        # --- 5. диагностика пересечения фиксированной цены (НЕ вердикт)
        o['touch_after'] = {}
        if len(snaps) >= 2:
            start_ask = min(float(x[0]) for _, _, a in snaps[:1] for x in a)
            for h in horizons:
                n_touch = n_obs = 0
                up = down = unchanged = 0
                tol = max(5.0, h * MAX_DELAY_FRAC)   # допустимое запаздывание
                for i, (rcv, b, a) in enumerate(snaps):
                    bb = max(float(x[0]) for x in b)
                    if bb < start_ask:
                        continue
                    n_touch += 1
                    target = rcv + h
                    j = i
                    while j < len(snaps) and snaps[j][0] < target - tol:
                        j += 1
                    if j >= len(snaps):
                        continue                  # будущего наблюдения нет: censored
                    delay = abs(snaps[j][0] - target)
                    if delay > tol:
                        continue                  # слишком поздний снимок: не наблюдение
                    n_obs += 1
                    _, bj, aj = snaps[j]
                    mid_j = (max(float(x[0]) for x in bj) + min(float(x[0]) for x in aj)) / 2
                    mid_i = (bb + min(float(x[0]) for x in a)) / 2
                    if mid_j > mid_i:
                        up += 1
                    elif mid_j < mid_i:
                        down += 1
                    else:
                        unchanged += 1
                o['touch_after'][str(h)] = {
                    'n_touch': n_touch,
                    'n_observed': n_obs,
                    'n_unobserved': n_touch - n_obs,
                    'mid_up': up, 'mid_down': down, 'mid_unchanged': unchanged,
                    'note': 'диагностика пересечения фиксированной цены (не вердикт)',
                }
            o['touch_start_ask'] = round(start_ask, 6)
        out['pairs'].append(o)
    return out


def main():
    ap = argparse.ArgumentParser(description='Экономический анализ maker-сбора')
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--cutoff-ns', type=int, default=None)
    ap.add_argument('--json-out', default=None)
    ap.add_argument('--latest', action='store_true')
    args = ap.parse_args()
    base = args.data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'maker')
    run_id = args.run_id
    if args.latest or not run_id:
        runs = sorted({os.path.basename(f).replace('_depth.jsonl', '')
                       for f in glob.glob(os.path.join(base, '*_depth.jsonl'))})
        run_id = run_id or (runs[-1] if runs else None)
    if not run_id:
        print('no run found')
        return
    res = econ_report(base, run_id, cutoff_ns=args.cutoff_ns)
    jout = args.json_out or os.path.join(os.path.dirname(base), 'reports',
                                         f'econ_{run_id}.json')
    os.makedirs(os.path.dirname(jout), exist_ok=True)
    with open(jout, 'w') as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(f"econ {run_id}: window_h={res['window_h']} ({res['window_sources']})")
    for p in res['pairs']:
        rt = p.get('roundtrip_by_budget', {})
        en = p.get('entry_by_budget', {})
        print(f"  {p['symbol']:<13} n={p.get('n_trades')} notional={p.get('notional_usdt')} "
              f"USDT ({p.get('notional_per_hour')}/ч) buy={p.get('n_buy')} sell={p.get('n_sell')} "
              f"entry5/10/25={en.get('5',{}).get('full_entry_pct')}/"
              f"{en.get('10',{}).get('full_entry_pct')}/{en.get('25',{}).get('full_entry_pct')}% "
              f"rt5/10/25={rt.get('5',{}).get('full_rt_pct')}/"
              f"{rt.get('10',{}).get('full_rt_pct')}/{rt.get('25',{}).get('full_rt_pct')}% "
              f"bad={res['bad_lines']} unparsed={p.get('unparsed_ts')}")
    print(f"  -> {jout}")


if __name__ == '__main__':
    main()
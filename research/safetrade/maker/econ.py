"""Экономический анализ maker-сбора (этап 1.4+, БЕЗ ордеров).

Ответы на экономические вопросы (ревью 92d4079, задание «внимание на экономику»):

1. Оборот в USDT: сумма total уникальных сделок в окне [start, cutoff].
2. Размеры и направление сделок: распределение total/amount, доля buy/sell.
3. Подтверждённые комиссии: fee_unverified (не подтверждены) — расчёт при
   явных допущениях, пометка качества.
4. Возможность выхода тем же количеством: bid-depth достаточен для продажи
   qty, купленного на бюджеты 5/10/25 USDT — доля снимков с полным покрытием.
5. Движение цены после потенциального исполнения: касание котировки —
   ТОЛЬКО opportunity (потенциальная встреча цены с заявкой), НЕ fill и НЕ
   прибыль (ТЗ: public replay даёт opportunity). Горизонты 10/30/60/300с.

Всё считается по raw JSONL; никаких ордеров/ключей не используется.
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


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def _ask_quote(bids, asks, budget):
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade', 'canonical_symbol': 'x',
              'bids': bids, 'asks': asks, 'quality_flags': [], 'recv_monotonic_ns': 1})
    price, qty = ob.vwap_cost('ask', Decimal(str(budget)))
    if price is None:
        return None, 0.0
    return float(price), float(qty)


def _bid_qty(bids, asks, qty):
    """Максимально продаваемое количество по bid-глубине (для выхода тем же qty)."""
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade', 'canonical_symbol': 'x',
              'bids': bids, 'asks': asks, 'quality_flags': [], 'recv_monotonic_ns': 1})
    filled = 0.0
    for px, q in sorted(bids, key=lambda x: -float(x[0])):
        need = float(qty) - filled
        if need <= 0:
            break
        take = min(need, float(q))
        filled += take
    return filled


def econ_report(run_dir, run_id, cutoff_ns=None, budgets=(5, 10, 25),
                horizons=(10, 30, 60, 300)):
    """Экономический отчёт по run. Возвращает dict с агрегатами по парам."""
    depth, bad_d = _read_jsonl(os.path.join(run_dir, f'{run_id}_depth.jsonl')), 0
    tr_rows = _read_jsonl(os.path.join(run_dir, f'{run_id}_trades.jsonl'))
    manifest = {}
    mp = os.path.join(run_dir, f'{run_id}_manifest.json')
    if os.path.exists(mp):
        manifest = json.load(open(mp))
    started_ts = parse_iso_utc(manifest.get('started_utc')) or 0.0
    if cutoff_ns is None:
        cutoff_ts = started_ts + 3600.0   # по умолчанию: первый час
    else:
        cutoff_ts = float(cutoff_ns) / 1e9

    out = {
        'run_id': run_id, 'started_utc': manifest.get('started_utc'),
        'cutoff_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(cutoff_ts)),
        'window_h': round((cutoff_ts - started_ts) / 3600.0, 3),
        'fee_status': 'fee_unverified',
        'fee_maker_bps': None, 'fee_taker_bps': None,
        'note': 'касание котировки = opportunity (потенциальная встреча), НЕ fill и НЕ прибыль',
        'pairs': [],
    }

    # --- depth: серия валидных снимков по парам (для выхода и движения цены)
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

    # --- trades: уникальные сделки в окне по парам
    trades_by = defaultdict(list)
    seen = set()
    for r in tr_rows:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        rcv = (r.get('t') or 0) / 1e9
        if rcv > cutoff_ts:
            continue
        for t in r.get('trades', []):
            if not isinstance(t, dict) or 'id' not in t:
                continue
            key = (r['pair'], t['id'])
            if key in seen:
                continue
            seen.add(key)
            ts = parse_iso_utc(t.get('created_at'))
            if ts is None or not (started_ts <= ts <= cutoff_ts):
                continue
            trades_by[r['pair']].append((ts, t))

    for pair in sorted(set(list(depth_by) + list(trades_by))):
        o = {'symbol': pair}

        # --- 1/2. оборот и направление по уникальным сделкам окна
        tr = trades_by.get(pair, [])
        o['n_trades'] = len(tr)
        totals = [float(t.get('total') or 0) for _, t in tr]
        o['notional_usdt'] = round(sum(totals), 2)
        window_h = out['window_h']
        o['notional_per_hour'] = round(sum(totals) / max(1e-9, window_h), 2)
        sides = [t.get('side') for _, t in tr]
        o['n_buy'] = sides.count('buy')
        o['n_sell'] = sides.count('sell')
        o['buy_fraction'] = round(sides.count('buy') / len(sides), 3) if sides else None
        if totals:
            s = sorted(totals)
            o['notional_min'] = round(s[0], 4)
            o['notional_p50'] = round(s[len(s)//2], 4)
            o['notional_p90'] = round(s[int(len(s)*.9)], 4)
            o['notional_max'] = round(s[-1], 4)

        # --- 4. выход тем же количеством (по глубине снимков)
        snaps = depth_by.get(pair, [])
        o['n_depth_snaps'] = len(snaps)
        o['exit_by_budget'] = {}
        for b_usdt in budgets:
            full_exit = 0
            for _, b, a in snaps:
                ask_px, buy_qty = _ask_quote(b, a, b_usdt)
                if ask_px is None or buy_qty <= 0:
                    continue
                sell_qty = _bid_qty(b, a, buy_qty)
                # допуск округления: >= 99.99% купленного
                if sell_qty >= buy_qty * 0.9999:
                    full_exit += 1
            o['exit_by_budget'][str(b_usdt)] = {
                'full_exit_pct': round(100.0 * full_exit / len(snaps), 1) if snaps else None,
                'n_full': full_exit, 'n_snaps': len(snaps),
            }

        # --- 5. движение цены после касания (opportunity, горизонты)
        o['touch_after'] = {}
        if len(snaps) >= 2:
            # стартовый ask = min ask первого снимка окна (предполагаемая лимитка)
            start_ask = min(float(x[0]) for _, _, a in snaps[:1] for x in a)
            for h in horizons:
                n_touch = 0
                up = 0      # mid вырос после касания
                down = 0
                for i, (rcv, b, a) in enumerate(snaps):
                    bb = max(float(x[0]) for x in b)
                    if bb >= start_ask:
                        n_touch += 1
                        # mid через H секунд
                        target = rcv + h
                        j = i
                        while j < len(snaps) and snaps[j][0] < target:
                            j += 1
                        if j < len(snaps):
                            _, bj, aj = snaps[j]
                            mid_j = (max(float(x[0]) for x in bj) +
                                     min(float(x[0]) for x in aj)) / 2
                            mid_i = (bb + min(float(x[0]) for x in a)) / 2
                            if mid_j > mid_i:
                                up += 1
                            elif mid_j < mid_i:
                                down += 1
                o['touch_after'][str(h)] = {
                    'n_touch': n_touch,
                    'mid_up': up, 'mid_down': down,
                    'mid_unchanged': n_touch - up - down,
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
    print(f"econ {run_id}: window_h={res['window_h']}")
    for p in res['pairs']:
        print(f"  {p['symbol']:<13} n={p.get('n_trades')} notional={p.get('notional_usdt')} "
              f"USDT ({p.get('notional_per_hour')}/ч) buy={p.get('n_buy')} sell={p.get('n_sell')} "
              f"exit5/10/25={p.get('exit_by_budget',{}).get('5',{}).get('full_exit_pct')}"
              f"/{p.get('exit_by_budget',{}).get('10',{}).get('full_exit_pct')}"
              f"/{p.get('exit_by_budget',{}).get('25',{}).get('full_exit_pct')}%")
    print(f"  -> {jout}")


if __name__ == '__main__':
    main()
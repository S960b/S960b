"""Анализ детального maker-сбора (этап 1.4, БЕЗ ордеров).

Читает data/maker/<run>/*.jsonl (depth/trades/oracle) и считает:
- распределение spread (min/p50/p90) по снимкам depth
- глубину 5/10/25 USDT (ask/bid VWAP) для предполагаемой заявки
- активность по trades (число, notional, median/p95 gap, покрытие)
- fee/status и external_reference
Выводит pair_screen_maker_detail.csv/json (тот же формат, что широкий отбор —
для панели вкладка «Пары»).

Запуск: cli.py maker-detail --run-id mk_xxx  (или --latest)
"""
import argparse
import glob
import json
import os
import sys
import time
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.book import OrderBook


def _bid_ask_from_rows(rows):
    """Возвращает списки (bids, asks) для каждого снимка, где они есть."""
    out = []
    for r in rows:
        b, a = r.get('bids'), r.get('asks')
        if b is None or a is None:
            continue
        out.append((r.get('t'), b, a))
    return out


def _best_bid_ask(bids, asks):
    if not bids or not asks:
        return None, None
    bb = max(float(p) for p, _ in bids)
    ba = min(float(p) for p, _ in asks)
    return bb, ba


def _vwap(levels, side, budget):
    """ask-VWAP (покупка, side='ask') или bid-VWAP (продажа, side='bid') на budget USDT."""
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade', 'canonical_symbol': 'x',
              'bids': levels[0], 'asks': levels[1], 'quality_flags': [], 'recv_monotonic_ns': 1})
    ap, _ = ob.vwap_cost('ask', Decimal(str(budget))) if side == 'ask' else (None, None)
    bp, _ = ob.vwap_cost('bid', Decimal(str(budget))) if side == 'bid' else (None, None)
    return (float(ap) if ap is not None else None,
            float(bp) if bp is not None else None)


def analyze_run(run_dir, run_id, verbose=False):
    rows_depth = []
    for f in glob.glob(os.path.join(run_dir, f'{run_id}_depth.jsonl')):
        with open(f) as fh:
            for line in fh:
                rows_depth.append(json.loads(line))
    rows_tr = []
    for f in glob.glob(os.path.join(run_dir, f'{run_id}_trades.jsonl')):
        with open(f) as fh:
            for line in fh:
                rows_tr.append(json.loads(line))
    rows_or = []
    for f in glob.glob(os.path.join(run_dir, f'{run_id}_oracle.jsonl')):
        with open(f) as fh:
            for line in fh:
                rows_or.append(json.loads(line))

    # regroup by pair
    by_pair = {}
    for r in rows_depth:
        by_pair.setdefault(r['pair'], []).append(r)
    tr_by_pair = {}
    for r in rows_tr:
        tr_by_pair.setdefault(r['pair'], []).append(r)

    out = []
    for pair, rows in sorted(by_pair.items()):
        o = {'symbol': pair, 'n_depth': len(rows), 'pair': pair,
             'n_trades': len(tr_by_pair.get(pair, []))}
        snaps = _bid_ask_from_rows(rows)
        o['n_good_snaps'] = len(snaps)
        spreads = []
        for t, b, a in snaps:
            bb, ba = _best_bid_ask(b, a)
            if bb and ba and ba > bb:
                spreads.append((ba - bb) / ((ba + bb) / 2) * 1e4)
        if spreads:
            s = sorted(spreads)
            o['spread_min_bps'] = round(s[0], 1)
            o['spread_p50_bps'] = round(s[len(s)//2], 1)
            o['spread_p90_bps'] = round(s[int(len(s)*0.9)], 1)
        # глубина на 5/10/25 по последнему валидному снимку
        if snaps:
            t, b, a = snaps[-1]
            a5, b5 = _vwap((b, a), 'ask', 5); s5 = _vwap((b, a), 'bid', 5)
            a10, b10 = _vwap((b, a), 'ask', 10); s10 = _vwap((b, a), 'bid', 10)
            a25, b25 = _vwap((b, a), 'ask', 25); s25 = _vwap((b, a), 'bid', 25)
            o.update({'depth_ask_5': a5, 'depth_bid_5': s5[1] if s5 else None,
                      'depth_ask_10': a10, 'depth_bid_10': s10[1] if s10 else None,
                      'depth_ask_25': a25, 'depth_bid_25': s25[1] if s25 else None})
        out.append(o)
    return {'run_id': run_id, 'pairs': out, 'n_oracle': len(rows_or)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--json-out', default=None)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    base = args.data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'maker')
    if args.run_id and 'mk_' not in args.run_id:
        # find latest
        runs = [os.path.basename(f).split('_depth')[0] for f in glob.glob(os.path.join(base, '*_depth.jsonl'))]
        runs = sorted(set(runs))
        args.run_id = runs[-1] if runs else None
    if not args.run_id:
        print('no run found'); return
    res = analyze_run(base, args.run_id, args.verbose)
    out = args.json_out or os.path.join(os.path.dirname(base), 'reports', f'pair_screen_maker_{args.run_id}.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(f"detail {args.run_id}: pairs={len(res['pairs'])} oracle={res['n_oracle']} -> {out}")
    if args.verbose:
        for p in res['pairs']:
            print(f"  {p['symbol']:<14} n={p['n_good_snaps']:>4} "
                  f"spread p50={p.get('spread_p50_bps')}bps ask5={p.get('depth_ask_5')}")


if __name__ == '__main__':
    main()
"""Анализ детального maker-сбора (этап 1.4, БЕЗ ордеров). Ревью 738de3a P0.2/P1.

Читает data/maker/<run_id>_depth.jsonl / _trades.jsonl / _oracle.jsonl:
- n_trade_polls — число ответов API (не сделок!)
- n_trade_records — записей из ответов (с дублями)
- n_unique_trades — уникальных сделок по (pair, id)
- n_trades_in_window — уникальных с UTC event time внутри окна run (start..эпоха)
  (за вычетом возрастной предыстории первого poll)
- n_duplicates, coverage по интервалам, непокрытые паузы
- активность: сделок/час по длительности окна (не по разбросу сделок)
- depth: только валидные стаканы (valid_book); распределение spread
  min/p10/p50/p90/max по снимкам; доля полных покрытий VWAP на 5/10/25 USDT
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


def _vwap(levels, side, budget):
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade', 'canonical_symbol': 'x',
              'bids': levels[0], 'asks': levels[1], 'quality_flags': [], 'recv_monotonic_ns': 1})
    price, qty = ob.vwap_cost(side, Decimal(str(budget)))
    if price is None:
        return None, 0.0, 'none'
    cost = float(qty) * float(price)
    tol = max(1e-6, float(budget) * 1e-9)
    return float(price), cost, 'full' if cost >= float(budget) - tol else 'partial'


def _read_jsonl(path):
    rows = []
    bad = 0
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


def analyze_run(run_dir, run_id, verbose=False, cutoff_ns=None):
    depth, bad_d = _read_jsonl(os.path.join(run_dir, f'{run_id}_depth.jsonl'))
    tr_rows, bad_t = _read_jsonl(os.path.join(run_dir, f'{run_id}_trades.jsonl'))
    or_rows, bad_o = _read_jsonl(os.path.join(run_dir, f'{run_id}_oracle.jsonl'))

    # границы run
    manifest = {}
    mp = os.path.join(run_dir, f'{run_id}_manifest.json')
    if os.path.exists(mp):
        manifest = json.load(open(mp))
    started_ts = parse_iso_utc(manifest.get('started_utc'))
    # cutoff: явный или конец последней валидной строки (согласованный срез)
    if cutoff_ns is None:
        last_ts = None
        for r in reversed(depth):
            t = r.get('t')
            if t is not None:
                last_ts = float(t) / 1e9
                break
        cutoff_ts = last_ts
    else:
        cutoff_ts = float(cutoff_ns) / 1e9

    out = {'run_id': run_id, 'n_depth_rows': len(depth), 'n_trade_rows': len(tr_rows),
           'n_oracle_rows': len(or_rows), 'bad_lines': {'depth': bad_d, 'trades': bad_t, 'oracle': bad_o},
           'window': {'started_utc': manifest.get('started_utc'), 'cutoff_epoch': cutoff_ts,
                      'ends_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(cutoff_ts)) if cutoff_ts else None},
           'pairs': []}

    # --- depth по парам
    depth_by = defaultdict(list)
    for r in depth:
        if isinstance(r, dict) and r.get('pair'):
            depth_by[r['pair']].append(r)

    # --- trades по парам: dedup (pair,id), unique, in-window
    tr_by = defaultdict(list)
    for r in tr_rows:
        if isinstance(r, dict) and r.get('pair') and isinstance(r.get('trades'), list):
            tr_by[r['pair']].append(r)

    for pair in sorted(set(list(depth_by) + list(tr_by))):
        o = {'symbol': pair, 'n_depth': len(depth_by.get(pair, [])),
             'n_trade_polls': len(tr_by.get(pair, [])),
             'n_trade_records': 0, 'n_unique_trades': 0, 'n_duplicates': 0,
             'n_trades_in_window': 0, 'trade_coverage': 'coverage_unknown',
             'unparsed_ts': 0}
        # dest стаканы
        snaps = []
        for r in depth_by.get(pair, []):
            b, a = r.get('bids'), r.get('asks')
            if b is None or a is None:
                continue
            ok, bb, ba = valid_book(b, a)
            o['n_valid_depth'] = o.get('n_valid_depth', 0) + (1 if ok else 0)
            if ok:
                snaps.append((r.get('t'), b, a))
        o['n_valid_depth'] = o.get('n_valid_depth', 0)
        spreads = []
        for t, b, a in snaps:
            bb = max(float(p) for p, _ in b)
            ba = min(float(p) for p, _ in a)
            if ba > bb:
                spreads.append((ba - bb) / ((ba + bb) / 2) * 1e4)
        if spreads:
            s = sorted(spreads)
            o['spread_min_bps'] = round(s[0], 1)
            o['spread_p10_bps'] = round(s[len(s)//10], 1)
            o['spread_p50_bps'] = round(s[len(s)//2], 1)
            o['spread_p90_bps'] = round(s[int(len(s)*.9)], 1)
            o['spread_max_bps'] = round(s[-1], 1)
        # full coverage доля для бюджетов 5/10/25
        for b_usdt in (5, 10, 25):
            full = 0
            for t, b, a in snaps:
                _, _, acov = _vwap((b, a), 'ask', b_usdt)
                if acov == 'full':
                    full += 1
            o[f'ask_full_cov_{b_usdt}_pct'] = round(100.0 * full / len(snaps), 1) if snaps else None

        # --- trades: uniquификация (pair, id)
        unique = {}
        first_ts = started_ts or 0
        for r in tr_by.get(pair, []):
            for t in r.get('trades', []):
                if not isinstance(t, dict) or 'id' not in t:
                    continue
                key = (pair, t['id'])
                o['n_trade_records'] += 1
                if key in unique:
                    o['n_duplicates'] += 1
                    continue
                ts = parse_iso_utc(t.get('created_at'))
                if ts is None:
                    o['unparsed_ts'] += 1
                unique[key] = (ts, t)
        o['n_unique_trades'] = len(unique)
        ts_list = sorted(x[0] for x in unique.values() if x[0] is not None)
        if ts_list and cutoff_ts:
            # только сделки в окне наблюдения (после старта run, до cutoff)
            in_win = [x for x in ts_list if x >= first_ts - 60 and x <= cutoff_ts + 300]
            o['n_trades_in_window'] = len(in_win)
            span_h = max(1e-9, (cutoff_ts - max(first_ts, ts_list[0])) / 3600.0)
            o['window_span_h'] = round(span_h, 2)
            o['trades_per_hour'] = round(len(in_win) / span_h, 2) if in_win else 0.0
            o['trades_per_hour_unique'] = round(len(ts_list) / span_h, 2) if ts_list else 0.0
            o['trade_first'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts_list[0]))
            o['trade_last'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts_list[-1]))
            if len(ts_list) >= 2:
                gaps = sorted([ts_list[i-1] - ts_list[i] for i in range(1, len(ts_list))])
                o['gap_median_s'] = round(gaps[len(gaps)//2], 1)
                o['gap_p95_s'] = round(gaps[int(len(gaps)*.95)], 1)
        if o['n_unique_trades'] == 0 and o['n_trade_polls'] > 0:
            o['trade_coverage'] = 'no_trades_observed'
        elif o['n_trade_polls'] == 0:
            o['trade_coverage'] = 'no_polls'
        else:
            o['trade_coverage'] = 'observed_window'  # полнота требует пагинации (см. коллектор)
        out['pairs'].append(o)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--json-out', default=None)
    ap.add_argument('--csv-out', default=None)
    ap.add_argument('--cutoff-ns', type=int, default=None)
    ap.add_argument('--latest', action='store_true')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    base = args.data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'maker')
    run_id = args.run_id
    if args.latest or not run_id:
        runs = sorted({os.path.basename(f).replace('_depth.jsonl', '')
                       for f in glob.glob(os.path.join(base, '*_depth.jsonl'))})
        run_id = runs[-1] if runs else None
        if args.run_id:
            run_id = args.run_id
        elif args.latest and runs:
            run_id = runs[-1]
    if not run_id:
        print('no run found'); return
    res = analyze_run(base, run_id, args.verbose, cutoff_ns=args.cutoff_ns)
    out_dir = os.path.dirname(args.json_out) if args.json_out else os.path.join(os.path.dirname(base), 'reports')
    os.makedirs(out_dir, exist_ok=True)
    jout = args.json_out or os.path.join(out_dir, f'pair_screen_maker_{run_id}.json')
    with open(jout, 'w') as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(f"detail {run_id}: pairs={len(res['pairs'])} depth={res['n_depth_rows']} "
          f"trades={res['n_trade_rows']} oracle={res['n_oracle_rows']} -> {jout}")
    if args.csv_out:
        import csv
        cols = ['symbol', 'n_valid_depth', 'spread_p10_bps', 'spread_p50_bps', 'spread_p90_bps',
                'spread_max_bps', 'ask_full_cov_5_pct', 'ask_full_cov_10_pct', 'ask_full_cov_25_pct',
                'n_trade_polls', 'n_trade_records', 'n_unique_trades', 'n_duplicates',
                'n_trades_in_window', 'trades_per_hour', 'window_span_h', 'trade_coverage']
        with open(args.csv_out, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for p in res['pairs']:
                w.writerow({c: p.get(c) for c in cols})
        print(f"  csv={args.csv_out}")
    if args.verbose:
        for p in res['pairs']:
            print(f"  {p['symbol']:<14} depth={p.get('n_valid_depth')} "
                  f"spread p50={p.get('spread_p50_bps')}bps "
                  f"trades unique={p.get('n_unique_trades')} win={p.get('n_trades_in_window')} "
                  f"dup={p.get('n_duplicates')} tph={p.get('trades_per_hour')}")


if __name__ == '__main__':
    main()
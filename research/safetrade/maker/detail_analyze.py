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
    if started_ts is None:
        started_ts = 0.0
    # cutoff: явный или конец последней строки depth, ПОЛУЧЕННОЙ до воспроизв. среза
    if cutoff_ns is None:
        last_ts = None
        for r in reversed(depth):
            t = r.get('t')
            if t is not None:
                last_ts = float(t) / 1e9
                break
        cutoff_ts = last_ts if last_ts is not None else time.time()
    else:
        cutoff_ts = float(cutoff_ns) / 1e9

    out = {'run_id': run_id, 'n_depth_rows': len(depth), 'n_trade_rows': len(tr_rows),
           'n_oracle_rows': len(or_rows), 'bad_lines': {'depth': bad_d, 'trades': bad_t, 'oracle': bad_o},
           'window': {'started_utc': manifest.get('started_utc'),
                      'started_epoch': started_ts, 'cutoff_ns': int(cutoff_ts * 1e9),
                      'cutoff_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(cutoff_ts)),
                      'span_h': round((cutoff_ts - started_ts) / 3600.0, 4),
                      'note': 'ОКНО БЕЗ скрытых допусков: event time в [start, cutoff] '
                              'И receive time <= cutoff'},
           'pairs': []}

    # --- depth по парам: только снимки, полученные ВНУТРИ среза (receive t <= cutoff)
    depth_by = defaultdict(list)
    for r in depth:
        if isinstance(r, dict) and r.get('pair'):
            rcv = (r.get('t') or 0) / 1e9
            if rcv <= cutoff_ts:
                depth_by[r['pair']].append(r)

    # --- trades по парам: poll-записи получены внутри среза
    tr_by = defaultdict(list)
    for r in tr_rows:
        if isinstance(r, dict) and r.get('pair') and isinstance(r.get('trades'), list):
            rcv = (r.get('t') or 0) / 1e9
            if rcv <= cutoff_ts:
                tr_by[r['pair']].append(r)

    for pair in sorted(set(list(depth_by) + list(tr_by))):
        o = {'symbol': pair, 'n_depth': len(depth_by.get(pair, [])),
             'n_trade_polls': len(tr_by.get(pair, [])),
             'n_trade_records': 0, 'n_unique_trades': 0, 'n_duplicates': 0,
             'n_trades_in_window': 0, 'trade_coverage': 'coverage_unknown',
             'unparsed_ts': 0, 'poll_statuses': []}
        # стаканы: только валидные (finite, положительные, не crossed)
        snaps = []
        for r in depth_by.get(pair, []):
            b, a = r.get('bids'), r.get('asks')
            if b is None or a is None:
                continue
            ok, bb, ba = valid_book(b, a)
            if ok:
                snaps.append((r.get('t'), b, a))
        o['n_valid_depth'] = len(snaps)
        spreads = []
        for t, b, a in snaps:
            bb = max(float(p) for p, _ in b)
            ba = min(float(p) for p, _ in a)
            sp = (ba - bb) / ((ba + bb) / 2) * 1e4
            if sp < 0:
                continue      # crossed снимки уже отсеяны valid_book; защита
            spreads.append(sp)
        if spreads:
            s = sorted(spreads)
            # включём нулевой spread (locked book) в распределение (P1 0b71d28)
            o['spread_min_bps'] = round(s[0], 1)
            o['spread_p10_bps'] = round(s[len(s)//10], 1)
            o['spread_p50_bps'] = round(s[len(s)//2], 1)
            o['spread_p90_bps'] = round(s[int(len(s)*.9)], 1)
            o['spread_max_bps'] = round(s[-1], 1)
        for b_usdt in (5, 10, 25):
            full = sum(1 for t, b, a in snaps
                       if _vwap((b, a), 'ask', b_usdt)[2] == 'full')
            o[f'ask_full_cov_{b_usdt}_pct'] = round(100.0 * full / len(snaps), 1) if snaps else None

        # --- trades: дедуп (pair,id); окно [start, cutoff] без допусков
        unique = {}
        poll_coverage = []
        for r in tr_by.get(pair, []):
            pc = r.get('coverage')
            if pc:
                poll_coverage.append(pc)
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
        o['poll_statuses'] = poll_coverage
        o['n_unique_trades'] = len(unique)
        # event time в [start, cutoff]; receive ограничен выше (rcv <= cutoff)
        in_win = {k: (ts, t) for k, (ts, t) in unique.items()
                  if ts is not None and started_ts <= ts <= cutoff_ts}
        o['n_trades_in_window'] = len(in_win)
        span_h = max(1e-9, (cutoff_ts - started_ts) / 3600.0)
        o['window_span_h'] = round(span_h, 4)
        if in_win:
            o['trades_per_hour'] = round(len(in_win) / span_h, 4)
            ts_list = sorted(ts for ts, _ in in_win.values())
            o['trade_first'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts_list[0]))
            o['trade_last'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts_list[-1]))
            if len(ts_list) >= 2:
                # gaps = разность СОСЕДНИХ в возрастающей сортировке (неотрицательна)
                gaps = sorted(ts_list[i] - ts_list[i-1] for i in range(1, len(ts_list)))
                o['gap_median_s'] = round(gaps[len(gaps)//2], 1)
                o['gap_p95_s'] = round(gaps[int(len(gaps)*.95)], 1)
        # coverage: полнота по статусам poll (P0.4 0b71d28): единственный
        # no_trades при ok=True и coverage=full — real no_trades; любой
        # failed/truncated/unknown украшает статус
        if o['n_trade_polls'] == 0:
            o['trade_coverage'] = 'no_polls'
        elif o['n_trade_records'] == 0 and all(c in ('full', 'full_at_page', 'caught_up')
                                               for c in poll_coverage):
            o['trade_coverage'] = 'no_trades_observed'
        elif any(c in ('request_failed',) for c in poll_coverage):
            o['trade_coverage'] = 'request_failed'
        elif any(c in ('history_truncated', 'coverage_unknown', 'pagination_not_advancing')
                 for c in poll_coverage):
            o['trade_coverage'] = 'history_truncated'
        else:
            o['trade_coverage'] = 'observed_window'
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
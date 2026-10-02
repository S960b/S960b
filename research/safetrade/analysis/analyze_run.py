"""Reproducible single-session analysis and separate paper accounts."""
import csv
import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from .book import bbo_series, OrderBook, payload_dict
from .oracle import build_oracle_series, premium_series, prepare_mid_column, oracle_asof, oracle_columns
from .leadlag import lead_lag, coverage_stats
from .reach import test_A_reach, summarize_reach


def _utc(ts_ns):
    return datetime.fromtimestamp(int(ts_ns)/1e9, tz=timezone.utc).isoformat()


def mono_to_utc(mid_by_ex, t_mono):
    for s in mid_by_ex.values():
        times = s.mono_ns.to_numpy(dtype=np.int64)
        k = np.searchsorted(times, int(t_mono), side='right')-1
        if k >= 0 and 'utc_ns' in s:
            return int(s.utc_ns.iloc[k]) + int(t_mono)-int(s.mono_ns.iloc[k])
    return None


def _context(cfg, base_dir, df, symbol, kind):
    from simulator.paper import PaperSimulator
    df = df[df.canonical_symbol == symbol].copy()
    if df.empty:
        raise ValueError('No events for symbol')
    sessions = df[['run_id', 'boot_id']].drop_duplicates()
    if len(sessions) != 1:
        raise ValueError('Analyze one run/boot at a time')
    df = prepare_mid_column(df)
    series = {}
    for ex in cfg['sources']+[cfg['target']]:
        sub = df[(df.exchange == ex) & df._selected]
        series[ex] = pd.DataFrame({'mono_ns': sub.recv_monotonic_ns.to_numpy(dtype=np.int64),
                                  'utc_ns': sub.recv_utc_ns.to_numpy(dtype=np.int64),
                                  'mid': sub._mid.to_numpy(dtype=float)})
    series = {ex: s for ex, s in series.items() if not s.empty}
    ext = df[df.exchange.isin(cfg['sources'])]
    age = cfg['fitness']['max_bbo_age_s']
    r = build_oracle_series(ext, cfg['sources'], age, 3)
    safe = series.get(cfg['target'])
    oc = cfg['oracle']
    premium = premium_series(list(zip(safe.mono_ns, safe.mid)) if safe is not None else [], r,
                             oc['window_min']*60, oc.get('premium_min_points', 10), age,
                             oc.get('premium_min_span_s', oc['window_min']*30))
    sim = PaperSimulator(cfg, [symbol])
    sim.set_premium_series(premium[0], premium[1])
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=os.path.dirname(__file__), text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=os.path.dirname(__file__), text=True))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    report = {'report_type': kind, 'generated_utc': datetime.now(timezone.utc).isoformat(),
              'symbol': symbol, 'run_id': str(sessions.iloc[0].run_id), 'boot_id': str(sessions.iloc[0].boot_id),
              'code_revision': revision, 'code_dirty': dirty,
              'config_hash': hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
              'config': cfg, 'n_events': len(df), 'ready_for_live_trading': False,
              'period': {'start_utc': _utc(df.recv_utc_ns.min()), 'end_utc': _utc(df.recv_utc_ns.max()),
                         'span_s': float((df.recv_monotonic_ns.max()-df.recv_monotonic_ns.min())/1e9)},
              'limitations': ['Offline research; no orders sent',
                              'Receive-time lead/lag includes network latency',
                              'REST SafeTrade observations do not verify a subsecond opportunity',
                              'Execution assumes complete capped fills and quoted depth; no queue model',
                              'Fees and market precision require exchange verification before trading']}
    state_path = os.path.join(base_dir, cfg['sqlite_path'])
    if os.path.exists(state_path):
        import sqlite3
        with sqlite3.connect('file:'+state_path+'?mode=ro', uri=True) as con:
            row = con.execute('SELECT status FROM runs WHERE run_id=?', [report['run_id']]).fetchone()
        report['recording_status'] = row[0] if row else 'unknown'
    else:
        report['recording_status'] = 'unknown'
    report['synthetic'] = bool(len(df)) and all(payload_dict(e).get('fixture')
                                            for e in df.to_dict('records'))
    return df, series, r, premium, sim, report


def build_mid_series(df, exchange, symbol):
    return bbo_series(df[(df.exchange == exchange) & (df.canonical_symbol == symbol)])


def build_safe_book(df_events, qty=Decimal('0.001'), max_age_s=5, record_end_ns=None):
    if df_events.empty:
        return None
    prepared = prepare_mid_column(df_events.copy())
    ob = OrderBook()
    mid_ts, bid_ts, ask_ts, vwap_ts = [], [], [], []
    for ev in prepared[prepared._selected].to_dict('records'):
        try:
            ob.apply(ev)
        except (ValueError, ArithmeticError):
            ob.valid = False
        t = int(ev['recv_monotonic_ns'])
        m = ob.mid()
        mid_ts.append((t, float(m) if m is not None else None))
        bid_ts.append((t, float(ob.best_bid()) if ob.valid and ob.best_bid() else None))
        ask_ts.append((t, float(ob.best_ask()) if ob.valid and ob.best_ask() else None))
        price, filled = ob.vwap('bid', qty)
        vwap_ts.append((t, float(price) if ob.executable and price is not None and filled==qty else None))
    return {'mid_ts': mid_ts, 'bid_ts': bid_ts, 'ask_ts': ask_ts, 'bids_vwap_ts': vwap_ts,
            'qty': str(qty), 'max_age_s': max_age_s,
            'record_end_ns': int(record_end_ns if record_end_ns is not None else df_events.recv_monotonic_ns.max())}


def run_full_analysis(cfg, base_dir, df_all, symbol, args=None):
    df, series, r, premium, sim, report = _context(cfg, base_dir, df_all, symbol, 'research')
    report['coverage'] = coverage_stats(series)
    report['oracle'] = {'n_R_points': int(np.isfinite(r[:,1].astype(float)).sum()) if r is not None else 0,
                        'sources': cfg['sources'], 'min_sources': 3}
    pt, pb, pc = premium
    report['premium'] = {'n_points': len(pt), 'warmup_ok': bool(len(pt)),
                         'median_b': float(np.median(pb)) if len(pb) else None,
                         'p10_b': float(np.percentile(pb, 10)) if len(pb) else None,
                         'p90_b': float(np.percentile(pb, 90)) if len(pb) else None}
    safe = series.get(cfg['target'])
    report['lead_lag_W5'] = lead_lag({ex: s for ex, s in series.items() if ex in cfg['sources']}, safe,
                                    5, 30, 1, cfg['fitness']['max_bbo_age_s']) if safe is not None else {}
    start, end = int(df.recv_monotonic_ns.min()), int(df.recv_monotonic_ns.max())
    signals, funnel = sim.detect_signals(series, start, end, W_s=5, D_s=0)
    horizons = cfg['reach']['horizons_s']
    if args is not None and getattr(args, 'horizons', None):
        horizons = [float(x) for x in args.horizons.split(',')]
    book = build_safe_book(df[df.exchange == cfg['target']], max_age_s=cfg['fitness']['max_book_age_s'], record_end_ns=end)
    results = {i: test_A_reach(book, sig.R0, sig.F0, sig.t0_ns, horizons, 1, sig.direction)
               for i, sig in enumerate(signals)} if book is not None else {}
    report['reach_A'] = {'summary': summarize_reach(results, horizons, 'raw'),
                         'summary_adj': summarize_reach(results, horizons, 'adj'),
                         'time_meaning': 'First observed hit, not exact crossing time between messages'}
    report['signals_meta'] = {'n_signals': len(signals), 'n_evaluated': len(results)}
    report['funnel'] = funnel
    report['premium']['side_ratios'] = _side_ratio_stats(df, cfg, r)
    report['result'] = 'research_only' if safe is not None and report['oracle']['n_R_points'] else 'insufficient_data'
    _write_report(report, os.path.join(base_dir, 'reports'), f'analysis_{symbol}.json')
    print(f"analysis: run={report['run_id']} signals={len(signals)} premium_points={len(pt)}")
    return report


def _side_ratio_stats(df, cfg, r):
    if r is None:
        return {'n': 0}
    rows = []
    columns = oracle_columns(r)
    for ev in df[(df.exchange == cfg['target']) & (df.event_type == 'book_snapshot')].to_dict('records'):
        R = oracle_asof(r, int(ev['recv_monotonic_ns']), cfg['fitness']['max_bbo_age_s'], columns)
        book = OrderBook()
        try:
            book.apply(ev)
        except (ValueError, ArithmeticError):
            continue
        if R is not None and book.mid() is not None:
            rows.append((float(book.best_bid())/float(R), float(book.best_ask())/float(R), float(book.mid())/float(R)))
    return {'n': len(rows), **({k: float(np.median([v[i] for v in rows]))
            for i, k in enumerate(['bid_R_median', 'ask_R_median', 'mid_R_median'])} if rows else {})}


def run_paper(cfg, base_dir, df_all, symbol, args=None):
    from simulator.paper import OrderBookReplay
    df, series, r, premium, sim, report = _context(cfg, base_dir, df_all, symbol, 'paper')
    target = df[df.exchange == cfg['target']]
    replay = OrderBookReplay(target.to_dict('records'), cfg['fitness']['max_book_age_s'])
    replay.record_end_ns = int(df.recv_monotonic_ns.max())
    start, end = int(df.recv_monotonic_ns.min()), int(df.recv_monotonic_ns.max())
    report['initial_balance_usdt'] = cfg['paper']['initial_balance_usdt']
    report['fees_taker_bps'] = cfg['fees']['safetrade']['taker_bps']
    report['hypotheses'] = {}
    all_trades = []
    for name, hp in cfg['paper']['hypotheses'].items():
        signals, funnel = sim.detect_signals(series, start, end, hp['W_s'], hp['D_s'])
        if report['recording_status'] == 'failed':
            funnel['recording_failure'] = len(signals)
            signals = []
        result = sim.run_hypothesis(signals, replay, name, hp['W_s'], hp['D_s'], hp['H_s'],
                                    hp['L_ms'], cfg['paper']['sizes_usdt'], symbol)
        report['hypotheses'][name] = {**result['stats'], 'funnel': funnel}
        all_trades.extend(result['trades'])
    report['account_rule'] = 'Each hypothesis/delay/budget starts its own account; scenario PnLs must not be summed'
    report['execution_book'] = {'n_snapshots': int((target.event_type=='book_snapshot').sum()),
                                'n_observation_only': sum('rest_provisional' in (e.get('quality_flags') or []) for e in target.to_dict('records'))}
    out = os.path.join(base_dir, 'reports')
    _write_report(report, out, f'paper_{symbol}.json')
    fields = list(TradeFields())
    rows = []
    for tr in all_trades:
        row = asdict(tr)
        row['run_id'] = report['run_id']; row['boot_id'] = report['boot_id']
        rows.append(row)
    csv_path = os.path.join(out, f"paper_trades_{symbol}_{report['run_id']}.csv")
    with open(csv_path+'.tmp', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    os.replace(csv_path+'.tmp', csv_path)
    import shutil
    shutil.copyfile(csv_path, os.path.join(out, f'paper_trades_{symbol}.csv'))
    print(f"paper: scenarios={sum(len(h['scenarios']) for h in report['hypotheses'].values())}, entries={len(all_trades)}")
    return report


def TradeFields():
    from simulator.paper import Trade
    from dataclasses import fields
    return [f.name for f in fields(Trade)] + ['run_id', 'boot_id']


def _write_report(report, out_dir, fname):
    os.makedirs(out_dir, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    for name in (fname.replace('.json', f"_{report['run_id']}.json"), fname):
        path = os.path.join(out_dir, name)
        with open(path+'.tmp', 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(path+'.tmp', path)

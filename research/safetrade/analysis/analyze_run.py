"""Reproducible single-session analysis and separate paper accounts."""
import csv
import hashlib
import json
import math
import os
import shutil
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


def utc_to_mono(mid_by_ex, t_utc_ns):
    """UTC → monotonic через интерполяцию по сериям (для scoring_period окна)."""
    for s in mid_by_ex.values():
        if s is None or s.empty or 'utc_ns' not in s:
            continue
        u = s.utc_ns.to_numpy(dtype=np.int64)
        m = s.mono_ns.to_numpy(dtype=np.int64)
        k = np.searchsorted(u, int(t_utc_ns), side='right') - 1
        if k < 0:
            return int(m[0])
        if k + 1 >= len(u):
            return int(m[-1])
        span = u[k + 1] - u[k]
        if span <= 0:
            return int(m[k])
        frac = (int(t_utc_ns) - u[k]) / span
        return int(m[k] + frac * (m[k + 1] - m[k]))
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
    # scoring_period (ревью next_steps): если задано окно, сигналы считаются ТОЛЬКО в окне,
    # разогрев идёт на состояние/премию/MAD, но его сигналы в отчёт не входят
    loaded_start_utc = _utc(df.recv_utc_ns.min())
    loaded_end_utc = _utc(df.recv_utc_ns.max())
    scoring_start_mono = None
    if args is not None and getattr(args, 'window_start_ns', None):
        scoring_start_mono = utc_to_mono(series, args.window_start_ns)
        scoring_start_mono = max(start, scoring_start_mono or start)
        report['scoring_period'] = {'start_utc': _utc(args.window_start_ns), 'end_utc': loaded_end_utc,
                                    'warmup_start_utc': loaded_start_utc,
                                    'note': 'Signals are scored only inside the window; warmup feeds state/premium/MAD'}
    signals, funnel = sim.detect_signals(series,
                                         scoring_start_mono if scoring_start_mono is not None else start,
                                         end, W_s=5, D_s=0)
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
    # CSV диагностики сигналов (ревью P0.2/next_steps): свежесть baseline и будущее покрытие отдельно
    df_safe = df[df.exchange == cfg['target']]
    n_csv = _signal_diag_csv(signals, sim, r, df_safe, cfg, horizons,
                             os.path.join(base_dir, 'reports', f"signals_{symbol}_{report['run_id']}.csv"))
    # ревью next_steps: копируем alias всегда (при 0 сигналов файл содержит свежий заголовок),
    # чтобы панель не показывала данные предыдущего run
    if os.path.exists(os.path.join(base_dir, 'reports', f"signals_{symbol}_{report['run_id']}.csv")):
        shutil.copyfile(os.path.join(base_dir, 'reports', f"signals_{symbol}_{report['run_id']}.csv"),
                        os.path.join(base_dir, 'reports', f'signals_{symbol}.csv'))
    report['signals_csv'] = {'n_rows': n_csv, 'path': f'reports/signals_{symbol}.csv'}
    report['result'] = 'research_only' if safe is not None and report['oracle']['n_R_points'] else 'insufficient_data'
    _write_report(report, os.path.join(base_dir, 'reports'), f'analysis_{symbol}.json')
    print(f"analysis: run={report['run_id']} signals={len(signals)} premium_points={len(pt)} "
          f"signals_csv={n_csv}")
    return report


def _side_ratio_stats(df, cfg, r):
    """Распределение bid/R, ask/R, mid/R и спреда ОДНОГО снимка (ревью: медианы скрывают
    редкие отклонения — нужны min/p10/p50/p90 и доля ask<R)."""
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
        bid, ask, mid = book.best_bid(), book.best_ask(), book.mid()
        if R is None or mid is None or bid is None or ask is None or float(R) <= 0:
            continue
        rows.append((float(bid)/float(R), float(ask)/float(R), float(mid)/float(R),
                     (float(ask)-float(bid))/float(mid)))
    if not rows:
        return {'n': 0}
    stats = {'n': len(rows)}
    for i, k in enumerate(['bid_R', 'ask_R', 'mid_R', 'spread_mid']):
        v = np.array([x[i] for x in rows], dtype=float)
        stats[k + '_min'] = float(v.min())
        stats[k + '_p10'] = float(np.percentile(v, 10))
        stats[k + '_p50'] = float(np.percentile(v, 50))
        stats[k + '_p90'] = float(np.percentile(v, 90))
    ask = np.array([x[1] for x in rows])
    stats['ask_below_R_count'] = int((ask < 1.0).sum())
    stats['ask_below_R_pct'] = round(float((ask < 1.0).mean()) * 100, 2)
    return stats


def _signal_diag_csv(signals, sim, r, df_safe, cfg, horizons, out_path):
    """CSV диагностики каждого сигнала (ревью next_steps P0): последний снимок SafeTrade
    ДО/В момент t0 (никогда следующий), bid/ask/mid, отношения к R и F0, VWAP по
    бюджетам 10/25/50, раздельные статусы внешнего R и baseline SafeTrade.
    Отношения из stale-котировки помечаются статусом и не входят в статистику пригодных."""
    import csv as _csv
    import bisect as _bisect
    columns = oracle_columns(r) if r is not None else None
    budgets = cfg['paper']['sizes_usdt']
    # снапшоты SafeTrade: (mono, utc, ev) — только book_snapshot с реальным стаканом
    snaps = []
    if df_safe is not None and not df_safe.empty:
        sub = df_safe[df_safe.event_type == 'book_snapshot']
        for _, ev in sub.iterrows():
            try:
                ob = OrderBook(); ob.apply(ev.to_dict())
            except (ValueError, ArithmeticError):
                continue
            if ob.best_bid() is None or ob.best_ask() is None:
                continue
            snaps.append((int(ev['recv_monotonic_ns']), int(ev['recv_utc_ns']), ob))
    snaps.sort(key=lambda x: x[0])
    snap_times = [s[0] for s in snaps]

    fields = ['run_id', 'boot_id', 'canonical_symbol', 't0_mono_ns', 't0_utc',
              'direction', 'W_s', 'delta_R_bps', 'threshold_bps', 'R0', 'F0',
              'external_R_usable', 'external_max_age_s',
              'safe_quote_utc', 'safe_quote_age_s', 'safe_max_age_s',
              'safe_baseline_usable', 'safe_quote_status',
              'bid', 'ask', 'mid', 'spread_bps',
              'bid_R_ratio', 'ask_R_ratio', 'bid_F0_ratio', 'ask_F0_ratio',
              'premium_available', 'next_snap_utc'] + \
             [f'n_future_{int(h)}s' for h in horizons] + \
             [f'ask_vwap_{b}_price' for b in budgets] + \
             [f'ask_vwap_{b}_qty' for b in budgets] + \
             [f'ask_vwap_{b}_cost' for b in budgets] + \
             [f'ask_vwap_{b}_status' for b in budgets] + \
             [f'bid_vwap_{b}_price' for b in budgets] + \
             [f'bid_vwap_{b}_qty' for b in budgets] + \
             [f'bid_vwap_{b}_cost' for b in budgets] + \
             [f'bid_vwap_{b}_status' for b in budgets]
    ext_age = cfg['fitness']['max_bbo_age_s']
    safe_age = cfg['fitness']['max_book_age_s']
    run_id = str(df_safe.run_id.iloc[0]) if df_safe is not None and not df_safe.empty else None
    boot_id = str(df_safe.boot_id.iloc[0]) if df_safe is not None and not df_safe.empty else None
    n = 0
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path + '.tmp', 'w', newline='', encoding='utf-8') as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        if not signals:
            # ревью: при нуле сигналов всё равно пишем свежий заголовок; файл
            # перезаписывает старый alias, панель не показывает предыдущий run
            os.replace(out_path + '.tmp', out_path)
            return 0
        for sig in sorted(signals, key=lambda s: s.t0_ns):
            t0 = int(sig.t0_ns)
            row = {'run_id': run_id, 'boot_id': boot_id,
                   'canonical_symbol': (getattr(sim, 'symbols', None) or [None])[0],
                   't0_mono_ns': t0,
                   't0_utc': _utc(int(sig.signal_utc_ns)) if sig.signal_utc_ns else _utc(t0),
                   'direction': sig.direction, 'W_s': sig.W_s,
                   'R0': sig.R0, 'F0': sig.F0,
                   'external_max_age_s': ext_age, 'safe_max_age_s': safe_age}
            before = oracle_asof(r, t0 - int(sig.W_s * 1e9), ext_age, columns) if columns else None
            row['delta_R_bps'] = round(math.log(sig.R0 / float(before)) * 1e4, 3) if before else ''
            row['threshold_bps'] = round(sig.threshold * 1e4, 3)
            # внешний R доступен в t0 (не старше max_bbo_age_s)
            r_now = oracle_asof(r, t0, ext_age, columns) if columns else None
            row['external_R_usable'] = r_now is not None
            row['premium_available'] = sig.b is not None and sig.F0 is not None
            # последний снимок SafeTrade ДО/В t0
            k = _bisect.bisect_right(snap_times, t0) - 1
            if k < 0:
                row.update({'safe_quote_status': 'no_quote', 'safe_baseline_usable': False,
                            'next_snap_utc': _utc(snaps[0][1]) if snaps else None})
            else:
                smono, sutc, ob = snaps[k]
                age = (t0 - smono) / 1e9
                bid, ask, mid = ob.best_bid(), ob.best_ask(), ob.mid()
                row.update({'safe_quote_utc': _utc(sutc), 'safe_quote_age_s': round(age, 1),
                            'bid': float(bid), 'ask': float(ask), 'mid': float(mid),
                            'spread_bps': round((float(ask) - float(bid)) / float(mid) * 1e4, 2),
                            'bid_R_ratio': round(float(bid) / float(sig.R0), 6),
                            'ask_R_ratio': round(float(ask) / float(sig.R0), 6),
                            'bid_F0_ratio': round(float(bid) / float(sig.F0), 6) if sig.F0 else '',
                            'ask_F0_ratio': round(float(ask) / float(sig.F0), 6) if sig.F0 else '',
                            'safe_baseline_usable': age <= safe_age and bid is not None and ask is not None,
                            'safe_quote_status': 'fresh' if age <= safe_age else 'stale'})
                # VWAP по бюджетам (quote-budget: лимит СТОИМОСТИ, не qty; при ask>mid
                # qty=budget/mid превысил бы бюджет — считаем по цене уровня, ревью next_steps)
                for b in budgets:
                    row[f'ask_vwap_{b}_price'] = row[f'ask_vwap_{b}_qty'] = \
                        row[f'ask_vwap_{b}_cost'] = row[f'ask_vwap_{b}_status'] = ''
                    row[f'bid_vwap_{b}_price'] = row[f'bid_vwap_{b}_qty'] = \
                        row[f'bid_vwap_{b}_cost'] = row[f'bid_vwap_{b}_status'] = ''
                    ap, af = ob.vwap_cost('ask', Decimal(str(b)))
                    bp, bf = ob.vwap_cost('bid', Decimal(str(b)))
                    if ap is not None:
                        row[f'ask_vwap_{b}_price'] = round(float(ap), 6)
                        row[f'ask_vwap_{b}_qty'] = str(af)
                        row[f'ask_vwap_{b}_cost'] = round(float(af) * float(ap), 2)
                        row[f'ask_vwap_{b}_status'] = 'ok' if float(af) * float(ap) >= float(b) * 0.99 else 'insufficient_depth'
                    if bp is not None:
                        row[f'bid_vwap_{b}_price'] = round(float(bp), 6)
                        row[f'bid_vwap_{b}_qty'] = str(bf)
                        row[f'bid_vwap_{b}_cost'] = round(float(bf) * float(bp), 2)
                        row[f'bid_vwap_{b}_status'] = 'ok' if float(bf) * float(bp) >= float(b) * 0.99 else 'insufficient_depth'
                if k + 1 < len(snaps):
                    row['next_snap_utc'] = _utc(snaps[k + 1][1])
            # будущие наблюдения на горизонтах (число снапшотов SafeTrade)
            for h in horizons:
                lim = t0 + int(h * 1e9)
                row[f'n_future_{int(h)}s'] = sum(1 for t in snap_times if t0 < t <= lim)
            w.writerow(row)
            n += 1
    os.replace(out_path + '.tmp', out_path)
    return n


def run_paper(cfg, base_dir, df_all, symbol, args=None):
    from simulator.paper import OrderBookReplay
    df, series, r, premium, sim, report = _context(cfg, base_dir, df_all, symbol, 'paper')
    target = df[df.exchange == cfg['target']]
    replay = OrderBookReplay(target.to_dict('records'), cfg['fitness']['max_book_age_s'])
    replay.record_end_ns = int(df.recv_monotonic_ns.max())
    start, end = int(df.recv_monotonic_ns.min()), int(df.recv_monotonic_ns.max())
    scoring_start_mono = None
    if args is not None and getattr(args, 'window_start_ns', None):
        scoring_start_mono = utc_to_mono(series, args.window_start_ns)
        scoring_start_mono = max(start, scoring_start_mono or start)
        report['scoring_period'] = {'start_utc': _utc(args.window_start_ns),
                                    'end_utc': _utc(df.recv_utc_ns.max()),
                                    'note': 'Paper entries only from signals inside the window; warmup feeds state'}
    report['initial_balance_usdt'] = cfg['paper']['initial_balance_usdt']
    report['fees_taker_bps'] = cfg['fees']['safetrade']['taker_bps']
    report['hypotheses'] = {}
    all_trades = []
    for name, hp in cfg['paper']['hypotheses'].items():
        sig_start = scoring_start_mono if scoring_start_mono is not None else start
        signals, funnel = sim.detect_signals(series, sig_start, end, hp['W_s'], hp['D_s'])
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

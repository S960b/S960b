#!/usr/bin/env python3
"""CLI проекта SafeTrade Research: doctor, discover, collect, analyze, replay, dashboard, paper.
Пример: python cli.py collect --minutes 5 ; python cli.py analyze --symbol BTCUSDT"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time

import numpy as np
import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("cli")


def load_cfg(path=None):
    path = path or os.path.join(BASE, "config", "config.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------- doctor ----------------
def cmd_doctor(args):
    cfg = load_cfg(args.config)
    print("== doctor ==")
    ok = True
    for pkg in ("numpy", "websockets", "pandas", "pyarrow", "duckdb", "streamlit", "plotly", "yaml"):
        try:
            __import__(pkg)
            print(f"  [ok] {pkg}")
        except ImportError as e:
            ok = False
            print(f"  [MISS] {pkg}: {e}")
    for ex in cfg["sources"] + [cfg["target"]]:
        try:
            from adapters import get_adapter
            a = get_adapter(ex)
            m = a.discover_markets()
            print(f"  [ok] {ex}: {len(m)} markets")
        except Exception as e:
            ok = False
            print(f"  [FAIL] {ex}: {type(e).__name__}: {str(e)[:100]}")
    # время
    import urllib.request
    try:
        with urllib.request.urlopen("https://api.binance.com/api/v3/time", timeout=10) as r:
            server = json.loads(r.read())["serverTime"]
        print(f"  [ok] clock skew vs binance: {int(time.time()*1000 - server)} ms")
    except Exception as e:
        print(f"  [warn] clock check: {e}")
    print("doctor done:", "OK" if ok else "ISSUES FOUND")


# ---------------- discover ----------------
def cmd_discover(args):
    cfg = load_cfg(args.config)
    exs = args.exchanges or (cfg["sources"] + [cfg["target"]])
    out = {}
    for ex in exs:
        from adapters import get_adapter
        a = get_adapter(ex)
        try:
            m = a.discover_markets()
            out[ex] = m
            print(f"{ex}: {len(m)} markets")
            if args.verbose:
                for x in m[:5]:
                    print("   ", x)
        except Exception as e:
            print(f"{ex}: FAIL {e}")
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        print(f"saved {args.out}")


# ---------------- collect ----------------
def cmd_collect(args):
    cfg = load_cfg(args.config)
    from collector import Collector
    c = Collector(cfg, args.data_root or BASE)
    duration = args.minutes * 60 if args.minutes else None
    symbols = [s.upper() for s in args.symbols] if args.symbols else None
    print(f"collect: run={c.run_id} duration={args.minutes}min symbols={symbols}")
    asyncio.run(c.run(duration_s=duration, symbols=symbols))
    print(f"done: events={c.c['events_written']} raw={c.c['raw_written']} "
          f"dropped_ev={c.c['events_dropped']} dropped_raw={c.c['raw_dropped']}")


# ---------------- analyze / replay ----------------
def cmd_index(args):
    """Просканировать parquet и записать reports/parquet_index.json (ревью P1.4)."""
    cfg = load_cfg(args.config)
    from analysis.parquet_index import save_parquet_index
    parquet_dir = os.path.join(args.data_root or BASE, cfg["storage"]["parquet_dir"])
    reports_dir = os.path.join(args.data_root or BASE, cfg["storage"].get("reports_dir", "reports"))
    idx = save_parquet_index(parquet_dir, reports_dir)
    print(f"index: files={idx['files']} runs={len(idx['runs'])}")
    for r in idx["runs"][-5:]:
        print(f"  {r['run_id']} boot={r['boot_id']} n={r['n_events']} "
              f"start={r['start_utc_ns']/1e9:.0f} end={r['end_utc_ns']/1e9:.0f}")


def _max_utc_ns(parquet_dir, run_id=None, symbol=None):
    """Быстрый максимум recv_utc_ns: по ВЫБРАННОМУ run/символу (не по всем файлам).
    Возвращает (t_max_ns, n_files) или (None, 0) при отсутствии данных."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    files = []
    for root, _, fs in os.walk(parquet_dir):
        for f in fs:
            if f.endswith(".parquet") and not f.startswith("."):
                files.append(os.path.join(root, f))
    if not files:
        return None, 0
    if run_id:
        files = [f for f in files if f"_{run_id}_" in os.path.basename(f)]
    if not files:
        return None, 0
    try:
        d = ds.dataset(files, format="parquet")
        filt = None
        if symbol:
            filt = (ds.field("canonical_symbol") == symbol)
        col = d.to_table(columns=["recv_utc_ns"], filter=filt).column("recv_utc_ns")
        if col.num_chunks == 0 or col.length() == 0:
            return None, len(files)
        return int(pc.max(col).as_py()), len(files)
    except Exception:
        return None, len(files)


def _utc_to_mono(series_by_ex, utc_ns):
    """UTC (recv_utc_ns) → monotonic (recv_monotonic_ns) через интерполяцию по внешним сериям."""
    for s in series_by_ex.values():
        if 'utc_ns' not in s or s.empty:
            continue
        u = s.utc_ns.to_numpy(dtype=np.int64)
        m = s.mono_ns.to_numpy(dtype=np.int64)
        k = np.searchsorted(u, int(utc_ns), side='right') - 1
        if k < 0:
            return int(m[0])
        if k + 1 >= len(u):
            return int(m[-1])
        # линейная интерполяция monotonic по utc между соседними наблюдениями
        span = u[k + 1] - u[k]
        if span <= 0:
            return int(m[k])
        frac = (int(utc_ns) - u[k]) / span
        return int(m[k] + frac * (m[k + 1] - m[k]))
    return None


def _load_window(args, cfg, symbol):
    """Загрузка parquet последнего run с опциональным окном (--window-min).

    Ревью P1: фильтр времени (recv_utc_ns) действует в СКАНЕРЕ ДО материализации —
    загружаются только последние (window_min + warmup) минут выбранного run, а не
    весь run. --run-id и --window-min работают ВМЕСТЕ (ревью next_steps P0).
    Возвращает (df, window_start_ns_utc | None)."""
    from analysis import load_parquet_all
    parquet_dir = os.path.join(args.data_root or BASE, cfg["storage"]["parquet_dir"])
    reports_dir = os.path.join(args.data_root or BASE, cfg["storage"].get("reports_dir", "reports"))
    run_id = getattr(args, "run_id", None)
    max_runs = getattr(args, "runs", None) or 1
    w = getattr(args, "window_min", None)
    index_path = reports_dir if os.path.exists(os.path.join(reports_dir, "parquet_index.json")) else None

    if not w:
        df = load_parquet_all(parquet_dir, symbol=symbol, run_id=run_id, max_runs=max_runs,
                              index_path=index_path)
        return df, None
    # окно: границы считаем В РАМКАХ выбранного run и символа
    t_max, n_files = _max_utc_ns(parquet_dir, run_id=run_id, symbol=symbol)
    if t_max is None:
        raise RuntimeError(f"Нет данных parquet для run_id={run_id or 'latest'} symbol={symbol}: "
                           f"выполните `cli.py index` и проверьте каталог {parquet_dir}")
    warmup = cfg["oracle"]["window_min"] * 60
    # разогрев: премия + максимум W/MAD/cooldown из paper-конфига (ревью next_steps)
    paper_cfg = cfg.get("paper", {})
    warmup_w = max([h.get("W_s", 5) for h in paper_cfg.get("hypotheses", {}).values()] + [5])
    max_h = max([h.get("H_s", 60) for h in paper_cfg.get("hypotheses", {}).values()] + [60])
    warmup_extra = max(int(20 * warmup_w), int(paper_cfg.get("cooldown_s", 60)), int(max_h))
    warmup_total = warmup + warmup_extra
    t_min = t_max - (w * 60 + warmup_total) * 1e9
    df = load_parquet_all(parquet_dir, symbol=symbol, run_id=run_id, max_runs=max_runs,
                          t_min_ns=t_min, t_max_ns=t_max, index_path=index_path)
    window_start_ns = t_max - w * 60 * 1e9
    print(f"window: последние {w} мин → {len(df)} событий "
          f"(загружено {w + warmup_total/60:.0f} мин: окно {w} + разогрев {warmup_total/60:.0f}; files={n_files})")
    return df, int(window_start_ns)


def cmd_analyze(args):
    cfg = load_cfg(args.config)
    symbol = args.symbol or "BTCUSDT"
    # ревью: по умолчанию один последний run (monotonic разных сессий несравним)
    df_all, win_start = _load_window(args, cfg, symbol)
    if df_all.empty:
        print("no parquet data yet — run collect first")
        return
    args.window_start_ns = win_start
    print(f"analyze {symbol}: {len(df_all)} events, "
          f"exchanges={sorted(df_all.exchange.unique())}, "
          f"types={sorted(df_all.event_type.unique())}")
    print(f"time span: {df_all.recv_utc_ns.min()/1e9:.0f} .. {df_all.recv_utc_ns.max()/1e9:.0f} (unix)")
    from datetime import datetime, timezone
    t0 = datetime.fromtimestamp(df_all.recv_utc_ns.min()/1e9, tz=timezone.utc)
    t1 = datetime.fromtimestamp(df_all.recv_utc_ns.max()/1e9, tz=timezone.utc)
    print(f"UTC: {t0.isoformat()} .. {t1.isoformat()}")
    # сводка по биржам/типам
    for ex in sorted(df_all.exchange.unique()):
        sub = df_all[df_all.exchange == ex]
        print(f"  {ex}: {len(sub)} events, bbo={len(sub[sub.event_type=='bbo'])}, "
              f"snap={len(sub[sub.event_type=='book_snapshot'])}, delta={len(sub[sub.event_type=='book_delta'])}")
    # запуск полного анализа (лаги/достижение) в отдельных скриптах
    from analysis.analyze_run import run_full_analysis
    run_full_analysis(cfg, args.data_root or BASE, df_all, symbol, args)


# ---------------- paper ----------------
def cmd_paper(args):
    cfg = load_cfg(args.config)
    from analysis.analyze_run import run_paper
    symbol = args.symbol or "BTCUSDT"
    df_all, win_start = _load_window(args, cfg, symbol)
    if df_all.empty:
        print("no data"); return
    args.window_start_ns = win_start
    run_paper(cfg, args.data_root or BASE, df_all, symbol, args)


# ---------------- dashboard ----------------
def cmd_dashboard(args):
    cfg = load_cfg(args.config)
    subprocess_streamlit(cfg, args)


def subprocess_streamlit(cfg, args):
    import subprocess
    env = dict(os.environ, SAFETRADE_BASE=args.data_root or BASE, SAFETRADE_CONFIG=args.config or "")
    cmd = [sys.executable, "-m", "streamlit", "run",
           os.path.join(BASE, "dashboard", "app.py"), "--server.address", "127.0.0.1",
           "--server.port", str(args.port)]
    log.info("dashboard: %s", " ".join(cmd))
    raise SystemExit(subprocess.call(cmd, env=env))


def cmd_maker_screen(args):
    """Этап 1 maker-отбора: широкий скрининг всех USDT-пар SafeTrade БЕЗ ордеров."""
    import asyncio as _asyncio
    from maker.feasibility import run_screen, screen_to_csv, screen_to_json
    import pandas as pd

    # общие пары с внешними биржами (для external_reference финалистов)
    common = ({}, {}, {})
    try:
        from common_pairs import load_exchange_usdt
        common = load_exchange_usdt()
    except Exception as e:
        print(f"common pairs: не загружены ({e}) — external_reference=missing для всех")

    res = _asyncio.run(run_screen(usdt_only=True, depth_snaps=args.depth_snaps,
                                  trade_pages=args.trade_pages, rpm=args.rpm,
                                  common=common, verbose=args.verbose))
    base = args.data_root or BASE
    csv_path = os.path.join(base, 'reports', 'pair_screen_maker.csv')
    json_path = os.path.join(base, 'reports', 'pair_screen_maker.json')
    screen_to_csv(res, csv_path)
    screen_to_json(res, json_path)
    print(f"maker-screen: пар={len(res.pairs)} api_calls={res.api_calls} 429={res.blocks_429}")
    print(f"  csv={csv_path} json={json_path}")
    df = pd.DataFrame([p.to_row() for p in res.pairs])
    for st in ('pass', 'insufficient_evidence', 'fail'):
        sub = df[df.candidate_status == st]
        if not sub.empty:
            print(f"  {st}: {len(sub)} — {', '.join(sub.symbol.head(8))}{'...' if len(sub) > 8 else ''}")


def cmd_maker_collect(args):
    """Детальный сбор финалистов maker-отбора (этап 1.3, БЕЗ ордеров)."""
    import asyncio as _asyncio
    from maker.detail_collect import run_detail
    pairs = [p.upper() for p in (args.pairs or 'PRLUSDT,QUANTUSUSDT,TSCUSDT,USDCUSDT,LTCUSDT').split(',')]
    base = args.data_root or BASE
    print(f"maker-collect: pairs={pairs} minutes={args.minutes} rpm={args.rpm}")
    _asyncio.run(run_detail(pairs, args.minutes, args.rpm, base, verbose=args.verbose))


def cmd_maker_detail(args):
    """Анализ детального maker-сбора (спреды/глубина/активность, rev 738de3a P0.2)."""
    from maker.detail_analyze import analyze_run
    base = args.data_root or BASE
    run_id = args.run_id
    runs_dir = os.path.join(base, 'data', 'maker')
    if args.latest or not run_id:
        import glob
        runs = sorted({os.path.basename(f).replace('_depth.jsonl', '')
                       for f in glob.glob(os.path.join(runs_dir, '*_depth.jsonl'))})
        run_id = run_id or (runs[-1] if runs else None)
    if not run_id:
        print('нет run: запустите maker-collect')
        return
    res = analyze_run(runs_dir, run_id, verbose=args.verbose, cutoff_ns=args.cutoff_ns)
    import json as _json
    out = os.path.join(base, 'reports', f'pair_screen_maker_{run_id}.json')
    with open(out, 'w') as f:
        _json.dump(res, f, indent=1, ensure_ascii=False)
    print(f"maker-detail {run_id}: pairs={len(res['pairs'])} -> {out}")
    if args.csv:
        csv_path = os.path.join(base, 'reports', f'pair_screen_maker_{run_id}.csv')
        import csv as _csv
        cols = ['symbol', 'n_valid_depth', 'spread_p10_bps', 'spread_p50_bps', 'spread_p90_bps',
                'spread_max_bps', 'ask_full_cov_5_pct', 'ask_full_cov_10_pct', 'ask_full_cov_25_pct',
                'n_trade_polls', 'n_trade_records', 'n_unique_trades', 'n_duplicates',
                'n_trades_in_window', 'trades_per_hour', 'window_span_h', 'trade_coverage']
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            w = _csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for p in res.get('pairs', []):
                w.writerow({c: p.get(c) for c in cols})
        print(f"  csv={csv_path}")


def cmd_maker_econ(args):
    """Экономический анализ maker-сбора (оборот, выход, движение после касания)."""
    from maker.econ import econ_report
    base = args.data_root or BASE
    runs_dir = os.path.join(base, 'data', 'maker')
    run_id = args.run_id
    if args.latest or not run_id:
        import glob
        runs = sorted({os.path.basename(f).replace('_depth.jsonl', '')
                       for f in glob.glob(os.path.join(runs_dir, '*_depth.jsonl'))})
        run_id = run_id or (runs[-1] if runs else None)
    if not run_id:
        print('нет run: запустите maker-collect')
        return
    res = econ_report(runs_dir, run_id, cutoff_ns=args.cutoff_ns)
    out = os.path.join(base, 'reports', f'econ_{run_id}.json')
    import json as _json
    with open(out, 'w') as f:
        _json.dump(res, f, indent=1, ensure_ascii=False)
    print(f"maker-econ {run_id}: window_h={res['window_h']} -> {out}")
    for p in res['pairs']:
        e = p.get('exit_by_budget', {})
        print(f"  {p['symbol']:<13} n={p.get('n_trades')} notional={p.get('notional_usdt')} USDT "
              f"({p.get('notional_per_hour')}/ч) buy={p.get('n_buy')} sell={p.get('n_sell')} "
              f"exit5/10/25="
              f"{e.get('5',{}).get('full_exit_pct')}/{e.get('10',{}).get('full_exit_pct')}/"
              f"{e.get('25',{}).get('full_exit_pct')}%")


def main():
    p = argparse.ArgumentParser(description="SafeTrade research toolkit")
    p.add_argument("--config", default=None)
    p.add_argument("--data-root", default=None, type=os.path.abspath, help="каталог данных и отчётов; по умолчанию каталог проекта")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor"); sp.set_defaults(fn=cmd_doctor)
    sp = sub.add_parser("discover")
    sp.add_argument("--exchanges", nargs="*", default=None)
    sp.add_argument("--out", default=None)
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(fn=cmd_discover)

    sp = sub.add_parser("index")
    sp.set_defaults(fn=cmd_index)

    sp = sub.add_parser("collect")
    sp.add_argument("--minutes", type=float, default=None)
    sp.add_argument("--symbols", nargs="*", default=None)
    sp.set_defaults(fn=cmd_collect)

    sp = sub.add_parser("analyze")
    sp.add_argument("--symbol", default=None)
    sp.add_argument("--horizons", default=None)
    sp.add_argument("--runs", type=int, choices=[1], default=1, help="одна сессия; разные monotonic-часы не смешиваются")
    sp.add_argument("--run-id", default=None)
    sp.add_argument("--window-min", type=float, default=None,
                    help="анализировать только последние N минут сессии (память: полный run 1.9М событий ~OOM)")
    sp.set_defaults(fn=cmd_analyze)

    sp = sub.add_parser("paper")
    sp.add_argument("--symbol", default=None)
    sp.add_argument("--runs", type=int, choices=[1], default=1)
    sp.add_argument("--run-id", default=None)
    sp.add_argument("--window-min", type=float, default=None)
    sp.set_defaults(fn=cmd_paper)

    sp = sub.add_parser("dashboard")
    sp.add_argument("--port", type=int, default=8501)
    sp.set_defaults(fn=cmd_dashboard)

    sp = sub.add_parser("maker-screen")
    sp.add_argument("--depth-snaps", type=int, default=2, help="снимков depth на пару")
    sp.add_argument("--trade-pages", type=int, default=2, help="страниц public trades на пару")
    sp.add_argument("--rpm", type=float, default=20.0, help="общий бюджет запросов SafeTrade/мин")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(fn=cmd_maker_screen)

    sp = sub.add_parser("maker-collect")
    sp.add_argument("--pairs", default=None, help="список пар через запятую (по умолчанию финалисты)")
    sp.add_argument("--minutes", type=float, default=360.0, help="длительность сбора (по умолчанию 6ч)")
    sp.add_argument("--rpm", type=float, default=20.0, help="общий бюджет запросов SafeTrade/мин")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(fn=cmd_maker_collect)

    sp = sub.add_parser("maker-detail")
    sp.add_argument("--run-id", default=None)
    sp.add_argument("--latest", action="store_true")
    sp.add_argument("--cutoff-ns", type=int, default=None)
    sp.add_argument("--csv", action="store_true")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(fn=cmd_maker_detail)

    sp = sub.add_parser("maker-econ")
    sp.add_argument("--run-id", default=None)
    sp.add_argument("--latest", action="store_true")
    sp.add_argument("--cutoff-ns", type=int, default=None)
    sp.set_defaults(fn=cmd_maker_econ)

    sp = sub.add_parser("replay")
    sp.add_argument("--symbol", default=None)
    sp.add_argument("--run-id", default=None)
    sp.add_argument("--window-min", type=float, default=None)
    sp.set_defaults(fn=cmd_replay)

    args = p.parse_args()
    args.fn(args)


def cmd_replay(args):
    cfg = load_cfg(args.config)
    symbol = args.symbol or "BTCUSDT"
    df, _ = _load_window(args, cfg, symbol)
    if df.empty:
        print("no data"); return
    # воспроизводимый event replay: проверяем согласованность снапшотов/дельт и строим mid-ряд
    from analysis.replay_check import replay_check
    import hashlib as _hl
    rep = replay_check(df, symbol)
    rep['run_id'] = str(df.run_id.iloc[0])
    rep['n_events'] = len(df)
    rep['config_hash'] = _hl.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    print("replay check:", json.dumps(rep, indent=1))
    # JSON текущего run (ревью P0: проверяемость отчёта)
    out = os.path.join(args.data_root or BASE, cfg["storage"].get("reports_dir", "reports"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"replay_{symbol}.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print(f"saved reports/replay_{symbol}.json")


if __name__ == "__main__":
    main()
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
def _load_window(args, cfg, symbol):
    """Загрузка parquet последнего run с опциональным окном (--window-min).
    Без окна полная сессия может не влезть в память на живых данных (OOM на ~400К строк)."""
    from analysis import load_parquet_all
    run_id = getattr(args, "run_id", None)
    max_runs = getattr(args, "runs", None) or 1
    df = load_parquet_all(os.path.join(args.data_root or BASE, cfg["storage"]["parquet_dir"]),
                          symbol=symbol, run_id=run_id, max_runs=max_runs)
    if df.empty:
        return df
    w = getattr(args, "window_min", None)
    if w:
        cut = df.recv_utc_ns.max() - w * 60 * 1e9
        df = df[df.recv_utc_ns >= cut].reset_index(drop=True)
        print(f"window: последние {w} мин → {len(df)} событий")
    return df


def cmd_analyze(args):
    cfg = load_cfg(args.config)
    symbol = args.symbol or "BTCUSDT"
    # ревью: по умолчанию один последний run (monotonic разных сессий несравним)
    df_all = _load_window(args, cfg, symbol)
    if df_all.empty:
        print("no parquet data yet — run collect first")
        return
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
    df_all = _load_window(args, cfg, symbol)
    if df_all.empty:
        print("no data"); return
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
    df = _load_window(args, cfg, symbol)
    if df.empty:
        print("no data"); return
    # воспроизводимый event replay: проверяем согласованность снапшотов/дельт и строим mid-ряд
    from analysis.replay_check import replay_check
    rep = replay_check(df, symbol)
    print("replay check:", json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
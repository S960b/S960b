"""Полный анализ набора parquet (v2, по ревью): единый causal-оракул R (только внешние 3),
премия b(t) по прошлому, lead-lag без интерполяции, тесты A/B достижения, воронка отказов.
Пишет reports/analysis_<symbol>.json, papers бумажные."""
import json
import math
import os
import statistics
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .book import bbo_series
from .oracle import build_oracle_series, premium_series, F_from_premium, prepare_mid_column
from .leadlag import lead_lag, coverage_stats
from .reach import test_A_reach, test_B_executable, summarize_reach

GIT_VERSION = "v2.0.0-rev1"


def _utc(ts_ns):
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()


def mono_to_utc(mid_by_ex, t_mono):
    """Ближайший UTC для monotonic-момента: as-of по любому ряду с utc_ns (ревью п.10)."""
    for ex, s in mid_by_ex.items():
        if "utc_ns" not in s.columns or len(s) == 0:
            continue
        t = s["mono_ns"].values
        if t_mono < t[0] or t_mono > t[-1]:
            continue
        import bisect
        idx = bisect.bisect_right(t, t_mono) - 1
        return int(s.iloc[idx]["utc_ns"])
    return None


def build_mid_series(df, exchange, symbol):
    sub = df[(df.exchange == exchange) & (df.canonical_symbol == symbol)]
    if sub.empty:
        return None
    return bbo_series(sub)


def run_full_analysis(cfg, base_dir, df_all, symbol, args=None):
    out_dir = os.path.join(base_dir, "reports")
    os.makedirs(out_dir, exist_ok=True)
    report = {
        "report_type": "research", "code_version": GIT_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "restrictions": ["виртуальные данные; реальных ордеров нет; результаты — гипотезы"],
    }
    exchanges = list(cfg["sources"]) + [cfg["target"]]

    # готовим _mid для всех событий (единый формат)
    df_all = prepare_mid_column(df_all)
    mid_by_ex = {}
    for ex in exchanges:
        s = build_mid_series(df_all, ex, symbol)
        if s is not None and len(s) > 10:
            # переиспользуем _mid из авторского фрейма
            sub = df_all[(df_all.exchange == ex) & (df_all.canonical_symbol == symbol)].dropna(subset=["_mid"])
            if len(sub) > 10:
                s = pd.DataFrame({"mono_ns": sub["recv_monotonic_ns"].values,
                                  "mid": sub["_mid"].values,
                                  "utc_ns": sub["recv_utc_ns"].values}).sort_values("mono_ns")
            mid_by_ex[ex] = s
            print(f"  [{ex}] mid-series: {len(s)} points")
    if len([e for e in cfg["sources"] if e in mid_by_ex]) < 3:
        report["result"] = "insufficient data (need 3 external sources)"
        _write_report(report, out_dir, f"analysis_{symbol}.json")
        print("недостаточно данных для анализа"); return report

    t0 = max(s["mono_ns"].min() for s in mid_by_ex.values())
    t1 = min(s["mono_ns"].max() for s in mid_by_ex.values())
    report["period"] = {"start_utc": _utc(max(s["utc_ns"].min() for s in mid_by_ex.values())),
                        "end_utc": _utc(min(s["utc_ns"].max() for s in mid_by_ex.values())),
                        "span_s": (t1 - t0) / 1e9}
    print(f"  period: {report['period']['start_utc']} .. {report['period']['end_utc']} ({report['period']['span_s']:.0f}s)")

    # ---- единый оракул R (только внешние!) ----
    ext_events = df_all[(df_all.canonical_symbol == symbol) &
                        (df_all.exchange.isin(cfg["sources"]))].dropna(subset=["_mid"])
    r_series = build_oracle_series(ext_events, cfg["sources"],
                                   max_bbo_age_s=cfg["fitness"]["max_bbo_age_s"], min_sources=3)
    if r_series is None or len(r_series) < 30:
        report["result"] = "insufficient R-series (мало пересечений 3/3)"
        _write_report(report, out_dir, f"analysis_{symbol}.json")
        print("мало точек R"); return report
    report["oracle"] = {"n_R_points": len(r_series),
                        "n_3of3": int((r_series[:, 2] == 3).sum()),
                        "n_2of3": int((r_series[:, 2] == 2).sum())}
    print(f"  R-series: {len(r_series)} pts, 3/3={report['oracle']['n_3of3']}, 2/3={report['oracle']['n_2of3']}")

    # ---- премия b(t) causal (по прошлому) ----
    safe_mids = mid_by_ex.get(cfg["target"])
    if safe_mids is not None and len(safe_mids) > 20:
        prem_t, prem_b, prem_cov = premium_series(
            list(zip(safe_mids["mono_ns"].values, safe_mids["mid"].values)),
            r_series, window_s=cfg["oracle"]["window_min"] * 60)
        report["premium"] = {"n_points": len(prem_t),
                             "median_b": round(float(np.median(prem_b)), 6) if len(prem_b) else None,
                             "p10_b": round(float(np.percentile(prem_b, 10)), 6) if len(prem_b) else None,
                             "p90_b": round(float(np.percentile(prem_b, 90)), 6) if len(prem_b) else None,
                             "warmup_ok": len(prem_t) > 0}
        print(f"  premium: {len(prem_t)} b-points, median={report['premium']['median_b']}")
        # bid/R и ask/R и VWAP-сравнение (ревью п.9/П1): отдельно стороны
        sides = _side_ratio_stats(df_all, symbol, cfg["sources"], cfg["target"])
        report["premium"]["side_ratios"] = sides
    else:
        report["premium"] = {"n_points": 0, "warmup_ok": False,
                             "note": "мало mid SafeTrade — премия не определяется"}

    # ---- lead-lag (без interp) ----
    lag_res = {}
    if cfg["target"] in mid_by_ex:
        for ex in cfg["sources"]:
            if ex in mid_by_ex:
                ll = lead_lag({ex: mid_by_ex[ex]}, mid_by_ex[cfg["target"]], W_s=5,
                              max_lag_s=10, step_s=1, max_age_s=cfg["fitness"]["max_bbo_age_s"])
                lag_res[ex] = ll
    report["lead_lag_W5"] = lag_res
    report["coverage"] = coverage_stats(mid_by_ex)

    # ---- тест A (достижение mid SafeTrade R0/F0) ----
    horizons = cfg["reach"]["horizons_s"]
    safe_events = df_all[(df_all.exchange == cfg["target"]) & (df_all.canonical_symbol == symbol)]
    safe_book = build_safe_book(safe_events)
    if safe_book is not None:
        # премия-ряд в симулятор для F0 (ревью P1-9: один модуль R/b/F)
        prem_series_for_sim = None
        if report["premium"].get("n_points", 0) > 0:
            prem_series_for_sim = (prem_t, prem_b)
        resultsA, funnel, sigs_meta = test_A_over_period(
            safe_book, mid_by_ex, cfg, t0, t1, horizons, symbol, prem_series_for_sim)
        report["reach_A"] = resultsA
        report["funnel"] = funnel
        report["signals_meta"] = sigs_meta
        print(f"  reach A: signals={sigs_meta.get('n_signals')}, funnel={funnel}")
    else:
        report["reach_A"] = {"n_signals": 0, "note": "no usable SafeTrade book"}

    _write_report(report, out_dir, f"analysis_{symbol}.json")
    print(f"  report: {out_dir}/analysis_{symbol}.json")
    return report


def _side_ratio_stats(df_all, symbol, sources, target):
    """bid/R, ask/R, mid/R для SafeTrade (ревью п.9). Среднее по событиям снапшота."""
    rows = []
    snap = df_all[(df_all.exchange == target) & (df_all.canonical_symbol == symbol) &
                  (df_all.event_type == "book_snapshot")]
    if snap.empty:
        return {"note": "no snapshots"}
    # R as-of для каждого снапшота — грубо: средний mid внешних рядом по времени
    ext = df_all[(df_all.exchange.isin(sources)) & (df_all.canonical_symbol == symbol)].dropna(subset=["_mid"])
    ext_by_t = ext.sort_values("recv_monotonic_ns")
    et = ext_by_t["recv_monotonic_ns"].values
    em = ext_by_t["_mid"].values
    import bisect
    for _, ev in snap.iterrows():
        t = ev["recv_monotonic_ns"]
        vals = []
        for _ex in sources:
            sub = ext_by_t[ext_by_t.exchange == _ex]
            st_ = sub["recv_monotonic_ns"].values
            sm_ = sub["_mid"].values
            k = bisect.bisect_right(st_, t) - 1
            if 0 <= k and (t - st_[k]) / 1e9 < 30:
                vals.append(float(sm_[k]))
        if len(vals) < 2:
            continue
        R = float(np.median(vals))
        b = json.loads(ev["bids"]) if isinstance(ev["bids"], str) else (ev["bids"] or [])
        a = json.loads(ev["asks"]) if isinstance(ev["asks"], str) else (ev["asks"] or [])
        if not b or not a:
            continue
        bid, ask = float(b[0][0]), float(a[0][0])
        if R <= 0 or bid <= 0 or ask <= 0:
            continue
        rows.append({"t": _utc(t), "bid_R": bid / R, "ask_R": ask / R, "mid_R": (bid + ask) / 2 / R})
    if not rows:
        return {"note": "no comparable snapshot-R pairs"}
    import statistics as st
    return {
        "n": len(rows),
        "bid_R_median": round(st.median(r["bid_R"] for r in rows), 6),
        "ask_R_median": round(st.median(r["ask_R"] for r in rows), 6),
        "mid_R_median": round(st.median(r["mid_R"] for r in rows), 6),
        "bid_R_p10": round(np.percentile([r["bid_R"] for r in rows], 10), 6),
        "ask_R_p90": round(np.percentile([r["ask_R"] for r in rows], 90), 6),
    }


def build_safe_book(df_events):
    """Стакан SafeTrade + mid-ряд. Пустой df -> None."""
    from .book import OrderBook
    if df_events.empty:
        return None
    ob = OrderBook()
    mid_ts, bid_ts, ask_ts, bid_vwap_ts = [], [], [], []
    for _, ev in df_events.iterrows():
        ob.apply(ev.to_dict())
        b, a = ob.best_bid(), ob.best_ask()
        if b and a:
            mid_ts.append((ev["recv_monotonic_ns"], float((b + a) / 2)))
            bid_ts.append((ev["recv_monotonic_ns"], float(b)))
            ask_ts.append((ev["recv_monotonic_ns"], float(a)))
            # VWAP по бид-стороне для теста B (фиксированный размер — берём qty из mid)
            from decimal import Decimal
            _, filled_vwap = ob.vwap("bid", Decimal("0.001"))
            bid_vwap_ts.append((ev["recv_monotonic_ns"], float(filled_vwap)))
    return {"ob": ob, "mid_ts": mid_ts, "bid_ts": bid_ts, "ask_ts": ask_ts,
            "bids_vwap_ts": bid_vwap_ts}


def test_A_over_period(safe_book, mid_by_ex, cfg, t0_ns, t1_ns, horizons, symbol,
                       prem_series_for_sim=None):
    """Сигналы по единому оракулу (только внешние), затем тест A достижения + воронка."""
    from simulator.paper import PaperSimulator
    sim = PaperSimulator(cfg, [symbol])
    if prem_series_for_sim is not None:
        sim.set_premium_series(*prem_series_for_sim)
    sigs = sim.detect_signals(mid_by_ex, t0_ns, t1_ns, W_s=5, D_s=0)
    signals, funnel = sigs if isinstance(sigs, tuple) else (sigs, {})
    if isinstance(signals, tuple):
        signals, funnel = signals[0], signals[1]
    print(f"  signals: {len(signals)}, funnel: {funnel}")
    if not signals:
        return {"summary": {"n": 0, "note": "no signals in window"},
                "by_status": {}}, funnel, {"n_signals": 0}
    results_per_sig = []
    for sig in signals[:500]:
        rA = test_A_reach(safe_book, sig.R0, sig.F0, sig.t0_ns, horizons, tol_bps=1.0,
                          direction=sig.direction)
        results_per_sig.append(rA)
    summary = summarize_reach({i: r for i, r in enumerate(results_per_sig)}, horizons, key="raw")
    summary_adj = summarize_reach({i: r for i, r in enumerate(results_per_sig)}, horizons, key="adj")
    # примеры: UTC-время сигнала (ревью п.10: не monotonic как UTC)
    examples = []
    for i, sig in enumerate(signals[:5]):
        t_utc = mono_to_utc(mid_by_ex, sig.t0_ns)
        examples.append({"t0": _utc(t_utc) if t_utc else None,
                         "R0": round(sig.R0, 2),
                         "F0": round(sig.F0, 2) if sig.F0 else None,
                         "direction": sig.direction, "b": sig.b})
    return {"summary": summary, "summary_adj": summary_adj, "examples": examples}, funnel, {"n_signals": len(signals)}


def run_paper(cfg, base_dir, df_all, symbol, args=None):
    """Прогон paper-гипотез (v2): бумажный счёт, ledger. Использует тот же оракул R."""
    from simulator.paper import PaperSimulator, OrderBookReplay
    from decimal import Decimal
    out_dir = os.path.join(base_dir, "reports")
    os.makedirs(out_dir, exist_ok=True)

    df_all = prepare_mid_column(df_all)
    mid_by_ex = {}
    for ex in list(cfg["sources"]) + [cfg["target"]]:
        sub = df_all[(df_all.exchange == ex) & (df_all.canonical_symbol == symbol)].dropna(subset=["_mid"])
        if len(sub) > 10:
            mid_by_ex[ex] = pd.DataFrame({"mono_ns": sub["recv_monotonic_ns"].values,
                                          "mid": sub["_mid"].values,
                                          "utc_ns": sub["recv_utc_ns"].values}).sort_values("mono_ns")
    if len([e for e in cfg["sources"] if e in mid_by_ex]) < 3 or cfg["target"] not in mid_by_ex:
        print("недостаточно данных для paper"); return

    t0 = max(s["mono_ns"].min() for s in mid_by_ex.values()) + 60e9   # warm-up
    t1 = min(s["mono_ns"].max() for s in mid_by_ex.values())
    if t1 - t0 < 60e9:
        print("недостаточно длины записи"); return

    safe_events = df_all[(df_all.exchange == cfg["target"]) & (df_all.canonical_symbol == symbol)] \
        .sort_values("recv_monotonic_ns")
    ob_replay = OrderBookReplay([e.to_dict() for _, e in safe_events.iterrows()])

    sim = PaperSimulator(cfg, [symbol])
    pc = cfg["paper"]
    # единая премия из оракула (rev P1-9)
    ext_events = df_all[(df_all.canonical_symbol == symbol) &
                        (df_all.exchange.isin(cfg["sources"]))].dropna(subset=["_mid"])
    r_series = build_oracle_series(ext_events, cfg["sources"])
    if r_series is not None and cfg["target"] in mid_by_ex:
        safe_mids_list = list(zip(mid_by_ex[cfg["target"]]["mono_ns"].values,
                                  mid_by_ex[cfg["target"]]["mid"].values))
        prem_t, prem_b, _ = premium_series(safe_mids_list, r_series, cfg["oracle"]["window_min"] * 60)
        if len(prem_t):
            sim.set_premium_series(prem_t, prem_b)

    all_trades = []
    summary = {}
    for hypo_name, hp in pc["hypotheses"].items():
        sigs, funnel = sim.detect_signals(mid_by_ex, t0, t1, W_s=hp["W_s"], D_s=hp["D_s"])
        res = sim.run_hypothesis(sigs if not isinstance(sigs, tuple) else sigs[0], ob_replay,
                                 hypo_name, hp["W_s"], hp["D_s"], hp["H_s"],
                                 hp["L_ms"], pc["sizes_usdt"], symbol)
        all_trades.extend(res["trades"])
        summary[hypo_name] = {
            "n_signals": res["stats"]["n_signals"], "n_entries": res["stats"]["n_entries"],
            "n_trades": res["stats"]["n_trades"],
            "pnl_sum_usdt": float(res["stats"]["pnl_sum"]),
            "fees_sum_usdt": float(res["stats"]["fees_sum"]),
            "funnel": {k: int(v) for k, v in (funnel or {}).items()},
            "by_L": {str(k): {"n": v["n"], "pnl": float(v["pnl"])} for k, v in res["stats"]["by_L"].items()},
        }
        print(f"  hypo {hypo_name}: signals={summary[hypo_name]['n_signals']} trades={res['stats']['n_trades']}")

    report = {
        "report_type": "paper", "code_version": GIT_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "initial_balance_usdt": pc["initial_balance_usdt"],
        "fees_taker_bps": cfg["fees"]["safetrade"]["taker_bps"],
        "hypotheses": summary,
        "restrictions": ["ВИРТУАЛЬНАЯ ТОРГОВЛЯ; taker/taker по стакану SafeTrade; реальных ордеров нет"],
    }
    with open(os.path.join(out_dir, f"paper_{symbol}.json"), "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    import csv
    with open(os.path.join(out_dir, f"paper_trades_{symbol}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["hypo", "signal_utc_ns", "L_ms", "size_usdt", "entry_vwap",
                                          "exit_vwap", "qty", "pnl", "fees", "reach_raw", "reach_adj",
                                          "exit_reason"])
        w.writeheader()
        for tr in all_trades:
            w.writerow({"hypo": tr.hypo, "signal_utc_ns": tr.signal_utc_ns, "L_ms": tr.L_ms,
                        "size_usdt": tr.size_usdt, "entry_vwap": str(tr.entry_vwap),
                        "exit_vwap": str(tr.exit_vwap), "qty": str(tr.qty), "pnl": str(tr.pnl),
                        "fees": str(tr.fee_buy + tr.fee_sell), "reach_raw": tr.reach_raw,
                        "reach_adj": tr.reach_adj, "exit_reason": tr.exit_reason})
    print(f"  paper: {out_dir}/paper_{symbol}.json + csv ({len(all_trades)} trades)")
    return report


def _median_of(xs):
    return round(statistics.median(xs), 2) if xs else None


def compute_premium_series(mid_by_ex, cfg, symbol, window_s=1800):
    safe = mid_by_ex.get(cfg["target"])
    ext_events = None
    return None


def _write_report(report, out_dir, fname):
    with open(os.path.join(out_dir, fname), "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
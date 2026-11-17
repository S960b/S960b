#!/usr/bin/env python3
"""NEWS v2.5 observational Tier-2 READ-ONLY verifier and descriptive analyzer.

This module NEVER calls an LLM, an exchange, or any writer. It never creates
a SQLite database or derives a return from a censored/pending outcome.
Valid live records require a full three-arm binding backed by the original
wire bytes and attestation chain. Offline fixtures are reported separately.

Report values are descriptive research statistics, not an optimized strategy,
risk-adjusted promise, or proof of any market edge.
"""
import argparse
import json
import math
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import tier2 as T2
import tier2_integrity as INTEG
import v24_collector as COL

ARMS = ("news", "news_market", "market")
FEE_BPS = 20.0
SLIPPAGE_BPS = 5.0
MAX_ROWS = 100_000


class AnalysisError(RuntimeError):
    pass


def open_read_only(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise AnalysisError("database_not_found:" + p.name)
    con = sqlite3.connect("file:" + quote(str(p), safe="/") + "?mode=ro",
                          uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    return con


def _tables(con):
    return {x[0] for x in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _round(value):
    return round(value, 6)


def _arm_stats(records):
    if not records:
        return {"n": 0, "mean_net_bps": None, "mean_brier": None,
                "hit_rate": None, "mean_spot_return_bps": None,
                "long": 0, "short": 0, "flat": 0}
    return {
        "n": len(records),
        "mean_net_bps": _round(statistics.mean(x["net_bps"] for x in records)),
        "mean_brier": _round(statistics.mean(x["brier"] for x in records)),
        "hit_rate": _round(statistics.mean(x["net_bps"] > 0 for x in records)),
        "mean_spot_return_bps": _round(statistics.mean(
            x["spot_return_bps"] for x in records)),
        "long": sum(x["action"] == "LONG_PAPER" for x in records),
        "short": sum(x["action"] == "SHORT_PAPER" for x in records),
        "flat": sum(x["action"] == "FLAT" for x in records),
    }


def _event_outcome(con, event_id, triple, bundle):
    outcome = con.execute(
        "SELECT due_ms, price, observed_ms, source, drift_ms, label_state "
        "FROM tier2_outcomes WHERE event_id=?", (event_id,)).fetchone()
    if outcome is None:
        return {"state": "pending"}
    due, price, observed, source, drift, state = outcome
    if state == "censored":
        # Some old implementations stored price=0 as an internal sentinel.
        # NEVER count it as -10000 bps or as a missed trade.
        return {"state": "censored"}
    if state != "labelled":
        raise AnalysisError("unrecognised_outcome_state")
    expected_due = int(triple["entry_observed_ms"]) + int(triple["horizon_ms"])
    if (int(due) != expected_due or not math.isfinite(float(price))
            or float(price) <= 0
            or int(observed) < expected_due
            or int(drift) != int(observed) - expected_due
            or abs(int(drift)) > T2.MAX_DRIFT_MS):
        raise AnalysisError("outcome_fields_invalid")
    entry = float(triple["entry_price"])
    if not math.isfinite(entry) or entry <= 0:
        raise AnalysisError("entry_price_invalid")
    spot_bps = (float(price) - entry) / entry * 10_000
    return {"state": "labelled", "spot_return_bps": spot_bps,
            "up": 1 if spot_bps > 0 else 0}


def _arm_value(arm, exit_label):
    action = arm["action"]
    spot_bps = exit_label["spot_return_bps"]
    if action == "LONG_PAPER":
        net = spot_bps - FEE_BPS - SLIPPAGE_BPS
    elif action == "SHORT_PAPER":
        net = -spot_bps - FEE_BPS - SLIPPAGE_BPS
    elif action == "FLAT":
        net = 0.0
    else:
        raise AnalysisError("invalid_paper_action")
    prob = float(arm["p_up"])
    if not math.isfinite(prob) or not 0.0 <= prob <= 1.0:
        raise AnalysisError("invalid_probability")
    return {"action": action, "net_bps": net, "spot_return_bps": spot_bps,
            "brier": (prob - exit_label["up"]) ** 2}


def inspect_feed_lags(feed_db, now_ms):
    """Historical diagnostics only, NOT a modification to eligibility.

    Multiple filters can reject one event, so counts are independently
    overlapping; this is NOT an exact count of candidate() rejection reasons.
    """
    if feed_db is None:
        return {"status": "not_requested"}
    p = Path(feed_db)
    if not p.is_file():
        return {"status": "no_feed_db"}
    con = open_read_only(p)
    try:
        if "v24_events" not in _tables(con):
            return {"status": "no_events_table"}
        rows = con.execute(
            "SELECT category, published_ms, observed_ms FROM v24_events "
            "LIMIT ?", (MAX_ROWS + 1,)).fetchall()
        if len(rows) > MAX_ROWS:
            raise AnalysisError("feed_row_limit_exceeded")
        by_category = defaultdict(Counter)
        for cohort, pub, obs in rows:
            budget = T2.FRESHNESS_BUDGET_S.get(cohort)
            data = by_category[str(cohort)]
            data["total"] += 1
            if budget is None:
                data["unknown_category"] += 1
                continue
            if int(pub) > int(obs):
                data["future_publication"] += 1
            if int(obs) - int(pub) > budget * 1000:
                data["transport_lag_over_budget"] += 1
            if int(now_ms) - int(obs) > budget * 1000:
                data["observation_stale_now"] += 1
        return {"status": "ok", "classification": "overlapping_diagnostics_only",
                "does_not_change_protocol": True,
                "by_category": {k: dict(v) for k, v in sorted(by_category.items())}}
    finally:
        con.close()


def analyze(*, db_path, feed_db=None, now_ms=None,
            attestation_path=None, include_offline_fixtures=False):
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    report = {
        "version": "v25-tier2-readonly-r5",
        "mode": "read_only_no_model_no_trade",
        "db": str(Path(db_path)),
        "status": "ok",
        "counts": {
            "triples": 0, "verified_live": 0, "verified_offline_fixture": 0,
            "labelled_live": 0, "labelled_offline_fixture": 0,
            "pending": 0, "censored": 0, "invalid": 0,
        },
        "invalid": [],
        "arms_live": {a: _arm_stats([]) for a in ARMS},
        "arms_offline_fixture_diagnostic": {a: _arm_stats([]) for a in ARMS},
        "feed_lag_diagnostics": inspect_feed_lags(feed_db, now_ms),
        "caveat": "Descriptive observational paper metrics, not proof of edge",
    }
    if not Path(db_path).is_file():
        report["status"] = "no_tier2_db"
        return report
    con = open_read_only(db_path)
    try:
        required = {"tier2_triples", "tier2_arms", "tier2_outcomes",
                    "tier2_bindings"}
        if not required.issubset(_tables(con)):
            raise AnalysisError("required_tier2_tables_missing")
        ids = [x[0] for x in con.execute(
            "SELECT event_id FROM tier2_triples ORDER BY created_ms,event_id "
            "LIMIT ?", (MAX_ROWS + 1,))]
        if len(ids) > MAX_ROWS:
            raise AnalysisError("triple_row_limit_exceeded")
        by_arm_live = defaultdict(list)
        by_arm_offline = defaultdict(list)
        for event_id in ids:
            report["counts"]["triples"] += 1
            try:
                bundle = INTEG.verify_binding(
                    con, event_id, attestation_path=attestation_path,
                    require_live=False)
                mode = bundle["mode"]
                if mode not in ("live_raw", "offline_fixture"):
                    raise AnalysisError("unknown_binding_mode")
                kind = ("verified_live" if mode == "live_raw"
                        else "verified_offline_fixture")
                report["counts"][kind] += 1
                result = _event_outcome(con, event_id, bundle["triple"], bundle)
                state = result["state"]
                if state == "pending":
                    report["counts"]["pending"] += 1
                    continue
                if state == "censored":
                    report["counts"]["censored"] += 1
                    continue
                report["counts"]["labelled_live" if mode == "live_raw"
                                 else "labelled_offline_fixture"] += 1
                # Read-only fixture metrics are exposed under a labelled
                # DIAGNOSTIC key only when explicitly requested. They are
                # never added to the live observational sample.
                if mode == "offline_fixture" and not include_offline_fixtures:
                    continue
                target = by_arm_live if mode == "live_raw" else by_arm_offline
                for arm in bundle["arms"]:
                    if arm["arm"] not in ARMS:
                        raise AnalysisError("invalid_arm_name")
                    target[arm["arm"]].append(_arm_value(arm, result))
            except (INTEG.IntegrityError, T2.Tier2Error, AnalysisError,
                    FileNotFoundError, ValueError, KeyError) as exc:
                report["counts"]["invalid"] += 1
                report["invalid"].append(
                    {"event_id": event_id,
                     "reason": type(exc).__name__ + ":" + str(exc)[:120]})
        report["arms_live"] = {arm: _arm_stats(by_arm_live[arm])
                               for arm in ARMS}
        report["arms_offline_fixture_diagnostic"] = {
            arm: _arm_stats(by_arm_offline[arm]) for arm in ARMS}
        if report["counts"]["invalid"]:
            report["status"] = "invalid_evidence_found"
    finally:
        con.close()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(T2.DEFAULT_DB))
    ap.add_argument("--feed-db", default=None)
    ap.add_argument("--attestation", default=None)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--include-offline-fixtures", action="store_true",
                    help="show fixture maths only; NEVER add to live observations")
    args = ap.parse_args(argv)
    try:
        out = analyze(db_path=args.db, feed_db=args.feed_db,
                      now_ms=args.now_ms, attestation_path=args.attestation,
                      include_offline_fixtures=args.include_offline_fixtures)
    except (AnalysisError, sqlite3.Error) as exc:
        out = {"status": "refused", "reason": type(exc).__name__ + ":" + str(exc)}
    print(json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False))
    return 0 if out["status"] in ("ok", "no_tier2_db") else 4


if __name__ == "__main__":
    raise SystemExit(main())

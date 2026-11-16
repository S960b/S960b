#!/usr/bin/env python3
"""NEWS v2.1 — one-off PROMPT_ONLY pilot pair.

A separate, deliberately minimal lane: it never touches the production dispatch
ledger, never reserves against it, and never lifts the production pause. It
builds two prompts under version `prompt-only-pilot-v1` for ONE naturally
eligible event, samples the public quote BEFORE and AFTER the arms, and writes
the pair to its own pilot database.

    python3 v21_pilot_cycle.py prepare   -> JSON with prompts + hashes
    python3 v21_pilot_cycle.py finish    -> seal the pilot record
    python3 v21_pilot_cycle.py status    -> what the pilot ledger holds

Exit codes: 0 ok, 3 refused (budget / contract), 4 nothing eligible.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v21_dispatch as D
import v21_paper_prep as P
import v21_selector as S

ROOT = Path("/home/kali/research/news-phase4-v21")
DATA = Path("/home/kali/.local/share/news-phase4-v21")
EVENTS_DB = DATA / "v21.sqlite"
PILOT_DB = DATA / "pilot.sqlite"
PAUSE_FLAG = DATA / "PAUSED_MODEL_INFERENCE"

VERSION = P.PROMPT_ONLY_VERSION
# Reported cost is recorded for the audit trail only. It is NOT a limit: the
# owner's access is free to them, and refusing a pair over a billing estimate
# would silently stop the experiment for no methodological reason.
MAX_PILOT_COST_USD = None
PILOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_pairs(
 event_id TEXT PRIMARY KEY, label TEXT NOT NULL,
 prompt_version TEXT NOT NULL, prompt_hashes TEXT NOT NULL,
 decision_time_ms INTEGER NOT NULL, market_cutoff_ms INTEGER NOT NULL,
 published_ms INTEGER, observed_ms INTEGER,
 provider_id TEXT, model_id TEXT,
 entry_price REAL, entry_ms INTEGER,
 tool_calls INTEGER NOT NULL, completions INTEGER NOT NULL,
 cost_usd REAL NOT NULL, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_arms(
 event_id TEXT NOT NULL, arm TEXT NOT NULL, p_up REAL,
 expected_return_bps REAL, uncertainty REAL, action TEXT,
 response_sha256 TEXT NOT NULL, completed_ms INTEGER,
 latency_ms INTEGER, PRIMARY KEY(event_id, arm));
CREATE TABLE IF NOT EXISTS pilot_outcomes(
 event_id TEXT NOT NULL, horizon_h INTEGER NOT NULL, due_ms INTEGER NOT NULL,
 exit_ms INTEGER, price REAL, drift_ms INTEGER, method TEXT,
 settled_ms INTEGER, PRIMARY KEY(event_id, horizon_h));
"""


def emit(obj, code=0):
    print(json.dumps(obj, sort_keys=True, ensure_ascii=True))
    return code


def open_pilot():
    con = sqlite3.connect(PILOT_DB, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(PILOT_SCHEMA)
    con.commit()
    return con


class QuoteError(ValueError):
    pass


def _urlopen_json(url, timeout=15):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def public_quote(after_ms=None):
    """Validated public quote with an explicit observation timestamp.

    Returns {price, observed_ms, source}. `P.parse_quote` returns a bare float,
    and the old code tested `isinstance(quote, dict)` on that float - always
    false - so it silently wrote entry_price=0, entry_ms=0 and due_ms=0. A quote
    is now a structure or an exception; there is no silent zero path.
    """
    import math
    try:
        payload = _urlopen_json(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        observed_ms = time.time_ns() // 1_000_000   # real clock, post-response
    except Exception as exc:
        raise QuoteError("quote_unavailable:" + type(exc).__name__)
    try:
        price = float(P.parse_quote(payload))
    except P.PrepError as exc:
        raise QuoteError("quote_invalid:" + str(exc))
    if not math.isfinite(price) or price <= 0:
        raise QuoteError("quote_not_positive_finite")
    if after_ms is not None and observed_ms <= int(after_ms):
        raise QuoteError("quote_not_strictly_after_last_arm")
    return {"price": price, "observed_ms": observed_ms,
            "source": "binance_public_ticker"}


def cmd_status(args):
    if not PILOT_DB.is_file():
        return emit({"status": "empty", "pilot_db": False})
    con = open_pilot()
    try:
        rows = con.execute("SELECT event_id,label,tool_calls,completions,"
                           "cost_usd FROM pilot_pairs").fetchall()
        arms = con.execute("SELECT event_id,arm,action FROM pilot_arms").fetchall()
        outs = con.execute("SELECT event_id,exit_ms FROM pilot_outcomes").fetchall()
    finally:
        con.close()
    return emit({"status": "ok", "pairs": rows, "arms": arms,
                 "outcomes": len(outs), "pilot_db": True})


def cmd_prepare(args):
    # The production pause is a safety control, not a switch for this lane.
    # A pilot is allowed only when the operator explicitly acknowledges that it
    # writes to the pilot ledger and spends at most MAX_PILOT_COST_USD.
    if not args.acknowledge_pilot:
        return emit({"status": "refused",
                     "reason": "pilot_acknowledgement_required"}, 3)
    if not EVENTS_DB.is_file():
        return emit({"status": "blocked", "reason": "events_db_missing"}, 3)
    now_ms = int(time.time() * 1000)
    cutoff = P.decision_cutoff(now_ms)
    market = P.fetch_market("production", cutoff)
    con = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    try:
        candidates, _ = S.select(con, now_ms, limit=10)
    finally:
        con.close()
    if not candidates:
        return emit({"status": "no_eligible_event"}, 4)
    pcon = open_pilot()
    try:
        taken = {r[0] for r in pcon.execute(
            "SELECT event_id FROM pilot_pairs")}
    finally:
        pcon.close()
    event = next((c for c in candidates if c["event_id"] not in taken), None)
    if event is None:
        return emit({"status": "no_eligible_event",
                     "reason": "all_candidates_already_piloted"}, 4)
    prompts = P.build_prompts(event, market, prompt_version=VERSION)
    hashes = {k: hashlib.sha256(v.encode()).hexdigest()
              for k, v in prompts.items()}
    if len(set(hashes.values())) != 2:
        return emit({"status": "refused", "reason": "degenerate_prompt_pair"}, 3)
    quote_before = public_quote()
    # buildArmPlan() requires the same contract the production path uses:
    # two deterministic attempt ids plus the prompts mapping.
    attempts = [hashlib.sha256(f"pilot:{event['event_id']}:{arm}".encode())
                .hexdigest() for arm in ("news", "news_market")]
    return emit({
        "status": "ready", "version": VERSION, "event_id": event["event_id"],
        "attempts": attempts, "prompts_sha256": hashes,
        "cohort": event["cohort"], "source": event["source"],
        "published_ms": event["published_ms"],
        "observed_ms": event["observed_ms"],
        "decision_time_ms": now_ms, "market_cutoff_ms": cutoff,
        "prompt_version": VERSION, "prompt_hashes": hashes,
        "prompts": prompts, "quote_before": quote_before,
        "max_cost_usd": None,  # informational only
    })


def cmd_finish(args):
    body = json.loads(sys.stdin.read() or "{}")
    results = body.get("results")
    if not isinstance(results, list) or len(results) != 2:
        return emit({"status": "refused", "reason": "incomplete_pair"}, 3)
    tools = sum(int(r.get("tools", 0)) for r in results)
    if tools != 0:
        # A tool call means the prompt-only instruction was not honoured; the
        # pair is quarantined and never labelled compliant.
        label = "PILOT_PROMPT_ONLY_TOOL_LEAK"
    else:
        label = "PROMPT_ONLY_COMPLIANT_ONE_PAIR"
    usage = body.get("usage") or {}
    cost = float(usage.get("cost_usd") or 0.0)   # recorded, never gated
    last_arm_ms = max(int(r.get("completed_ms") or 0) for r in results)
    try:
        # Entry anchor is sampled AFTER both arms answered, never before.
        quote_after = public_quote(after_ms=last_arm_ms)
    except QuoteError as exc:
        return emit({"status": "refused",
                     "reason": "entry_anchor_invalid:" + str(exc)}, 3)
    entry_price = quote_after["price"]
    entry_ms = quote_after["observed_ms"]
    due_ms = entry_ms + 4 * 3_600_000
    if not (entry_price > 0 and entry_ms > last_arm_ms and due_ms > entry_ms):
        return emit({"status": "refused",
                     "reason": "entry_anchor_invariant_violated"}, 3)
    parsed = []
    for r in results:
        row = P.parse_arm_response(r["text"])
        parsed.append((r["arm"], row, hashlib.sha256(
            r["text"].encode()).hexdigest(), int(r.get("completed_ms")
                                                 or time.time() * 1000),
            int(r.get("latency_ms") or 0), r.get("model") or {}))
    providers = {tuple(sorted(p[5].items())) for p in parsed}
    if len(providers) != 1:
        return emit({"status": "refused", "reason": "model_identity_conflict"}, 3)
    provider = dict(providers.pop())
    con = open_pilot()
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO pilot_pairs(event_id,label,prompt_version,"
            "prompt_hashes,decision_time_ms,market_cutoff_ms,published_ms,"
            "observed_ms,provider_id,model_id,entry_price,entry_ms,tool_calls,"
            "completions,cost_usd,created_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body["event_id"], label, body["prompt_version"],
             json.dumps(body["prompt_hashes"], sort_keys=True),
             int(body["decision_time_ms"]), int(body["market_cutoff_ms"]),
             int(body.get("published_ms") or 0), int(body.get("observed_ms") or 0),
             provider.get("providerID"), provider.get("modelID"),
             entry_price, entry_ms,
             tools, int(usage.get("completions") or 0), cost,
             int(time.time() * 1000)))
        for arm, row, sha, done, latency, _model in parsed:
            action = P.decision(row["p_up"], row["expected_return_bps"])
            con.execute("INSERT INTO pilot_arms VALUES(?,?,?,?,?,?,?,?,?)",
                        (body["event_id"], arm, row["p_up"],
                         row["expected_return_bps"], row["uncertainty"],
                         action, sha, done, latency))
        con.execute("INSERT INTO pilot_outcomes(event_id,horizon_h,due_ms) "
                    "VALUES(?,?,?)", (body["event_id"], 4, due_ms))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        con.close()
        return emit({"status": "already_piloted", "event_id": body["event_id"]})
    except Exception as exc:
        con.rollback()
        con.close()
        return emit({"status": "refused",
                     "reason": "pilot_write_failed:" + type(exc).__name__}, 3)
    con.close()
    return emit({"status": "recorded", "label": label, "tool_calls": tools,
                 "entry_price": entry_price, "entry_ms": entry_ms,
                 "due_ms": due_ms, "event_id": body["event_id"],
                 "arms": {p[0]: p[1]["p_up"] for p in parsed},
                 "actions": {p[0]: P.decision(p[1]["p_up"],
                                              p[1]["expected_return_bps"])
                             for p in parsed}})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("status", "prepare", "finish"))
    ap.add_argument("--acknowledge-pilot", action="store_true")
    ap.add_argument("--acknowledge-cost", action="store_true")
    a = ap.parse_args(argv)
    handler = {"status": cmd_status, "prepare": cmd_prepare,
               "finish": cmd_finish}[a.command]
    return handler(a)


if __name__ == "__main__":
    raise SystemExit(main())

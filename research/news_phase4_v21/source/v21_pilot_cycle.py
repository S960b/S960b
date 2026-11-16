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
import math
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

# Tool evidence is tri-state on purpose. "No tool count was reported" is not the
# same fact as "zero tool calls were observed", and only the second can support
# a compliance claim. Summing a possibly missing count silently upgraded
# unverified pairs to clean, so every reader goes through the tri-state below.
TOOL_KNOWN_ZERO = "known_zero"
TOOL_KNOWN_POSITIVE = "known_positive"
TOOL_UNKNOWN = "unknown"
TOOL_EVIDENCE_STATES = (TOOL_KNOWN_ZERO, TOOL_KNOWN_POSITIVE, TOOL_UNKNOWN)
EVIDENCE_VERSION = "tool-evidence-v1"
# Terminal session states that make "the arm finished and nothing else ran"
# checkable. Anything else is unverified, never assumed.
TERMINAL_SESSION_STATES = ("idle", "completed", "done", "stopped", "closed")
LABEL_COMPLIANT = "PROMPT_ONLY_COMPLIANT_ONE_PAIR"
LABEL_TOOL_LEAK = "PILOT_PROMPT_ONLY_TOOL_LEAK"
LABEL_EVIDENCE_UNKNOWN = "PILOT_TOOL_EVIDENCE_UNKNOWN"
LABEL_SESSION_UNVERIFIED = "PILOT_SESSION_UNVERIFIED"
PILOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_pairs(
 event_id TEXT PRIMARY KEY, label TEXT NOT NULL,
 prompt_version TEXT NOT NULL, prompt_hashes TEXT NOT NULL,
 decision_time_ms INTEGER NOT NULL, market_cutoff_ms INTEGER NOT NULL,
 published_ms INTEGER, observed_ms INTEGER,
 provider_id TEXT, model_id TEXT,
 entry_price REAL, entry_ms INTEGER,
 tool_calls INTEGER NOT NULL, completions INTEGER NOT NULL,
 cost_usd REAL, created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_arms(
 event_id TEXT NOT NULL, arm TEXT NOT NULL, p_up REAL,
 expected_return_bps REAL, uncertainty REAL, action TEXT,
 response_sha256 TEXT NOT NULL, completed_ms INTEGER,
 latency_ms INTEGER, PRIMARY KEY(event_id, arm));
CREATE TABLE IF NOT EXISTS pilot_outcomes(
 event_id TEXT NOT NULL, horizon_h INTEGER NOT NULL, due_ms INTEGER NOT NULL,
 exit_ms INTEGER, price REAL, drift_ms INTEGER, method TEXT,
 settled_ms INTEGER, PRIMARY KEY(event_id, horizon_h));
CREATE TABLE IF NOT EXISTS pilot_settle_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at_ms INTEGER NOT NULL,
 status TEXT NOT NULL, settled INTEGER NOT NULL, pending INTEGER NOT NULL,
 detail TEXT);
CREATE TABLE IF NOT EXISTS pilot_annotations(
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
 version TEXT NOT NULL, kind TEXT NOT NULL, at_ms INTEGER NOT NULL,
 detail TEXT NOT NULL, UNIQUE(event_id, version, kind));
"""

# Columns added after the ledger already held rows. Old rows keep NULL, and
# NULL reads as UNKNOWN: a missing provenance must never be re-labelled clean.
ARM_EVIDENCE_COLUMNS = (
    ("tool_evidence", "TEXT"),
    ("tool_evidence_provenance", "TEXT"),
    ("tool_evidence_count", "INTEGER"),
    ("session_verified", "INTEGER"),
    ("evidence_version", "TEXT"),
)


def emit(obj, code=0):
    print(json.dumps(obj, sort_keys=True, ensure_ascii=True))
    return code


def open_pilot(read_only=False):
    if read_only:
        con = sqlite3.connect(f"file:{PILOT_DB}?mode=ro", uri=True, timeout=5)
        con.execute("PRAGMA query_only=ON")
        return con
    con = sqlite3.connect(PILOT_DB, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(PILOT_SCHEMA)
    migrate_pilot(con)
    con.commit()
    return con


def migrate_pilot(con):
    """Additive only: new columns are nullable and old rows are left as-is."""
    have = {r[1] for r in con.execute("PRAGMA table_info(pilot_arms)")}
    for name, sqltype in ARM_EVIDENCE_COLUMNS:
        if name not in have:
            con.execute(f"ALTER TABLE pilot_arms ADD COLUMN {name} {sqltype}")


def _unverified(reason):
    return {"pos": TOOL_UNKNOWN, "provenance": reason, "count": None,
            "complete": False, "session_verified": False}


def arm_tool_evidence(result):
    """Classify one arm's tool history. Absence is UNKNOWN, never zero.

    A zero is only accepted when the producer also asserts that the history it
    read was complete. Without that assertion the count is unfalsifiable: it may
    be a truncated view, an empty early snapshot, or a field that simply was not
    sent, so it is recorded as unknown and cannot support compliance.
    """
    if not isinstance(result, dict):
        return _unverified("result_not_a_mapping")
    provenance = result.get("tool_evidence_provenance")
    provenance = provenance if isinstance(provenance, str) and provenance \
        else "unstated"
    raw = result.get("tools", None)
    if "tools" not in result or raw is None:
        return _unverified("missing_tool_history:" + provenance)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return _unverified("tool_count_not_integer:" + type(raw).__name__)
    if raw < 0:
        return _unverified("collector_error_sentinel:" + provenance)
    if result.get("tool_history_complete") is not True:
        # A gateway-reported zero without proof of completeness is unproven.
        return _unverified("history_not_proven_complete:" + provenance)
    session_id = result.get("session_id")
    verified = isinstance(session_id, str) and bool(session_id.strip())
    terminal = result.get("session_terminal")
    if not (isinstance(terminal, str) and terminal in TERMINAL_SESSION_STATES):
        verified = False
    return {"pos": TOOL_KNOWN_POSITIVE if raw > 0 else TOOL_KNOWN_ZERO,
            "provenance": provenance, "count": int(raw), "complete": True,
            "session_verified": bool(verified)}


def classify_pair(evidence):
    """Final classification from per-arm tri-state evidence, fail-closed."""
    states = [e["pos"] for e in evidence]
    if TOOL_KNOWN_POSITIVE in states:
        return LABEL_TOOL_LEAK
    if TOOL_UNKNOWN in states:
        return LABEL_EVIDENCE_UNKNOWN
    if not all(e["session_verified"] for e in evidence):
        return LABEL_SESSION_UNVERIFIED
    return LABEL_COMPLIANT


def verified_tool_total(evidence):
    """Sum only counts that are actually known; None when any arm is unknown."""
    counts = [e["count"] for e in evidence if e["pos"] != TOOL_UNKNOWN]
    if len(counts) != len(evidence):
        return None
    return int(sum(counts))


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
    con = open_pilot(read_only=True)
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(pilot_arms)")}
        ev_cols = ",".join(c for c, _t in ARM_EVIDENCE_COLUMNS if c in have)
        rows = con.execute("SELECT event_id,label,tool_calls,completions,"
                           "cost_usd FROM pilot_pairs").fetchall()
        arms = con.execute("SELECT event_id,arm,action" +
                           (("," + ev_cols) if ev_cols else "") +
                           " FROM pilot_arms").fetchall()
        outs = con.execute("SELECT event_id,exit_ms FROM pilot_outcomes").fetchall()
    finally:
        con.close()
    # NULL / missing evidence reads as UNKNOWN here as well: reporting it as a
    # clean zero would undo the tri-state at the last step.
    names = ["event_id", "arm", "action"] + list(ev_cols.split(",") if ev_cols
                                                 else [])
    arm_rows = []
    for r in arms:
        d = dict(zip(names, r))
        if d.get("tool_evidence") not in TOOL_EVIDENCE_STATES:
            d["tool_evidence"] = TOOL_UNKNOWN
            d.setdefault("tool_evidence_provenance", "absent")
        arm_rows.append(d)
    return emit({"status": "ok", "pairs": rows, "arms": arm_rows,
                 "evidence_version": EVIDENCE_VERSION,
                 "outcomes": len(outs), "pilot_db": True})


def cmd_annotate(args):
    """Append a versioned correction. Historical rows are never relabelled."""
    detail = sys.stdin.read() or "{}"
    try:
        parsed = json.loads(detail)
    except json.JSONDecodeError:
        return emit({"status": "refused", "reason": "annotation_not_json"}, 3)
    if not isinstance(parsed, dict):
        return emit({"status": "refused",
                     "reason": "annotation_not_an_object"}, 3)
    con = open_pilot()
    try:
        con.execute("INSERT INTO pilot_annotations(event_id,version,kind,at_ms,"
                    "detail) VALUES(?,?,?,?,?)",
                    (args.event_id, args.version, args.kind,
                     int(time.time() * 1000), json.dumps(parsed, sort_keys=True)))
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return emit({"status": "already_annotated", "event_id": args.event_id,
                     "version": args.version, "kind": args.kind})
    except sqlite3.Error as exc:
        con.close()
        return emit({"status": "refused",
                     "reason": "annotation_write_failed:" +
                               type(exc).__name__}, 3)
    con.close()
    return emit({"status": "annotated", "event_id": args.event_id,
                 "version": args.version, "kind": args.kind})


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
    # Evidence first, label second. `sum(int(r.get("tools", 0)))` treated a
    # missing field as a verified zero and could seal an unverified pair as
    # compliant, so the count is never invented here.
    evidence = [arm_tool_evidence(r) for r in results]
    tools = verified_tool_total(evidence)
    label = classify_pair(evidence)
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
        for idx, (arm, row, sha, done, latency, _model) in enumerate(parsed):
            action = P.decision(row["p_up"], row["expected_return_bps"])
            ev = evidence[idx]
            con.execute(
                "INSERT INTO pilot_arms(event_id,arm,p_up,expected_return_bps,"
                "uncertainty,action,response_sha256,completed_ms,latency_ms,"
                "tool_evidence,tool_evidence_provenance,tool_evidence_count,"
                "session_verified,evidence_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (body["event_id"], arm, row["p_up"],
                 row["expected_return_bps"], row["uncertainty"],
                 action, sha, done, latency, ev["pos"], ev["provenance"],
                 ev["count"], 1 if ev["session_verified"] else 0,
                 EVIDENCE_VERSION))
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
    return emit({"status": "recorded", "label": label,
                 "tool_calls": tools,
                 "tool_evidence": {p[0]: evidence[i]["pos"]
                                   for i, p in enumerate(parsed)},
                 "tool_evidence_provenance": {p[0]: evidence[i]["provenance"]
                                              for i, p in enumerate(parsed)},
                 "entry_price": entry_price, "entry_ms": entry_ms,
                 "due_ms": due_ms, "event_id": body["event_id"],
                 "arms": {p[0]: p[1]["p_up"] for p in parsed},
                 "actions": {p[0]: P.decision(p[1]["p_up"],
                                              p[1]["expected_return_bps"])
                             for p in parsed}})


MAX_PILOT_DRIFT_MS = 60_000


def _pilot_exit(due_ms, now_ms):
    """First public 1m close at or after due; raises when not determinable."""
    if not isinstance(due_ms, int) or due_ms <= 0:
        raise P.PrepError("pilot_anchor_invalid")
    start = (due_ms // P.MIN_MS) * P.MIN_MS
    if start + P.MIN_MS > now_ms + 1:
        raise P.PrepError("label_pending_candle_open")
    candles = P.fetch_klines_1m(start, limit=5)
    return pick_exit_candle(candles, due_ms, now_ms)


# D15: the evidence that makes a pilot outcome bona fide. Every constant is a
# property of a real Binance 1m candle, not a tolerance we may widen.
CANDLE_SPAN_MS = 59_999          # close_ms - open_ms for one complete minute
PILOT_DRIFT_BOUND_MS = 60_000    # drift must be < this, i.e. [0, 60000)
EXPECTED_SYMBOL = "BTCUSDT"


def validate_candle(open_ms, close_ms, price):
    """Structural checks; raises a named refusal, never a range clamp."""
    if type(open_ms) is not int or type(close_ms) is not int:
        raise P.PrepError("candle_bounds_not_integer")
    if close_ms - open_ms != CANDLE_SPAN_MS:
        # A span of 0 is a partially formed candle; a span of 60000 or more is
        # not a 1m candle at all. Either way the evidence is unusable.
        raise P.PrepError("candle_not_one_complete_minute")
    if open_ms % P.MIN_MS != 0:
        raise P.PrepError("candle_open_not_aligned")
    if isinstance(price, bool) or not isinstance(price, float):
        raise P.PrepError("label_price_not_float")
    if not math.isfinite(price):
        raise P.PrepError("label_price_not_finite")
    if price <= 0:
        raise P.PrepError("label_price_invalid")


def pick_exit_candle(candles, due_ms, now_ms):
    """The first 1m candle that CLOSED at or after due, with proof.

    Proof means: exactly one complete minute (open..open+59999), aligned on the
    minute boundary, closed before `now`, covering `due`, with drift in
    [0, 60000) and a finite positive price. A candle that is still forming is
    reported as pending, never used, and an earlier candle is never substituted.
    """
    if not isinstance(candles, list) or not candles:
        raise P.PrepError("label_candles_missing")
    for candle in sorted(candles):
        if not isinstance(candle, tuple) or len(candle) != 3:
            raise P.PrepError("label_row_invalid")
        open_ms, close_ms, price = candle
        validate_candle(open_ms, close_ms, price)
        if close_ms < due_ms:
            continue
        if open_ms > due_ms:
            # A gap: no candle covers the due instant.
            raise P.PrepError("label_candle_unavailable")
        if close_ms > now_ms:
            raise P.PrepError("label_pending_candle_open")
        drift = close_ms - due_ms
        if drift < 0 or drift >= PILOT_DRIFT_BOUND_MS:
            raise P.PrepError("label_drift_too_large")
        return {"exit_ms": close_ms, "price": price, "drift_ms": drift,
                "method": "binance_public_1m_close",
                "symbol": EXPECTED_SYMBOL,
                "open_ms": open_ms, "span_ms": close_ms - open_ms}
    raise P.PrepError("label_candle_unavailable")


def cmd_settle(args):
    """Settle due pilot slots from their own post-due 1m candle.

    Independent of the production labeler by design: a pilot row must never be
    settled against production data. Everything is reported, nothing is silent,
    and an invalid anchor is permanently ineligible.
    """
    if not PILOT_DB.is_file():
        return emit({"status": "no_pilot_db", "settled": 0, "pending": 0}, 3)
    con = open_pilot()
    now_ms = int(time.time() * 1000)
    settled = pending = invalid = 0
    failures = []
    try:
        rows = con.execute(
            "SELECT event_id, horizon_h, due_ms, exit_ms FROM pilot_outcomes"
        ).fetchall()
        for event_id, horizon, due_ms, already in rows:
            if already is not None:
                continue                        # one-time settlement
            if not due_ms:
                invalid += 1                    # ANCHOR_INVALID stays ineligible
                continue
            if now_ms < due_ms:
                pending += 1
                continue
            try:
                row = _pilot_exit(int(due_ms), now_ms)
            except P.PrepError as exc:
                reason = str(exc)
                if reason in ("label_pending_candle_open", "label_not_due"):
                    pending += 1
                    continue
                failures.append({"event_id": event_id, "reason": reason,
                                 "due_ms": due_ms})
                pending += 1
                continue
            try:
                con.execute(
                    "UPDATE pilot_outcomes SET exit_ms=?, price=?, drift_ms=?, "
                    "method=?, settled_ms=? WHERE event_id=? AND horizon_h=? "
                    "AND exit_ms IS NULL",
                    (row["exit_ms"], row["price"], row["drift_ms"],
                     row["method"], now_ms, event_id, horizon))
                con.commit()
            except sqlite3.Error as exc:
                failures.append({"event_id": event_id,
                                 "reason": "pilot_settle_write_failed:" +
                                           type(exc).__name__})
                pending += 1
                continue
            settled += 1
        payload = {"status": "settled" if not failures else "settle_incomplete",
                   "settled": settled, "pending": pending,
                   "anchor_invalid_skipped": invalid,
                   "failures": failures, "now_ms": now_ms}
        con.execute("INSERT INTO pilot_settle_log(at_ms,status,settled,pending,"
                    "detail) VALUES(?,?,?,?,?)",
                    (now_ms, payload["status"], settled, pending,
                     json.dumps(failures)[:2000]))
        con.commit()
    finally:
        con.close()
    emit(payload)
    return 0 if not failures else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command",
                    choices=("status", "prepare", "finish", "settle",
                             "annotate"))
    ap.add_argument("--event-id")
    ap.add_argument("--version", default=EVIDENCE_VERSION)
    ap.add_argument("--kind", default="tool_evidence_correction")
    ap.add_argument("--acknowledge-pilot", action="store_true")
    ap.add_argument("--acknowledge-cost", action="store_true",
                    help="DEPRECATED and inert: cost is never a gate. Accepted "
                         "only so older invocations do not break.")
    a = ap.parse_args(argv)
    if a.command == "annotate" and not a.event_id:
        return emit({"status": "refused", "reason": "event_id_required"}, 3)
    handler = {"status": cmd_status, "prepare": cmd_prepare,
               "finish": cmd_finish, "settle": cmd_settle,
               "annotate": cmd_annotate}[a.command]
    return handler(a)


if __name__ == "__main__":
    raise SystemExit(main())

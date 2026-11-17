#!/usr/bin/env python3
"""NEWS v2.2 — three-arm ledger, writer and offline mock executor.

A separate database namespace on purpose: a v2.1 two-arm record must never be
mistaken for a v2.2 three-arm record, and a v2.2 run must never write into the
v2.1 tables. Nothing here touches production paths or the pilot lane.
"""
import hashlib
import json
import sqlite3
import time

import v21_paper_prep as P
import v22_arms as A

SCHEMA_V22 = """
CREATE TABLE IF NOT EXISTS v22_triples(
 event_id TEXT PRIMARY KEY,
 protocol_version TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 prompt_hashes TEXT NOT NULL,
 cohort TEXT NOT NULL,
 declared_cohort TEXT,
 effective_cohort TEXT,
 cohort_provenance TEXT,
 source TEXT,
 published_ms INTEGER,
 observed_ms INTEGER,
 cutoff_ms INTEGER NOT NULL,
 entry_price REAL NOT NULL,
 entry_observed_ms INTEGER NOT NULL,
 horizon_h INTEGER NOT NULL,
 last_arm_ms INTEGER NOT NULL,
 synthetic INTEGER NOT NULL DEFAULT 0,
 seal_sha256 TEXT NOT NULL,
 created_ms INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'sealed');
CREATE TABLE IF NOT EXISTS v22_arms(
 event_id TEXT NOT NULL, arm TEXT NOT NULL,
 attempt_id TEXT NOT NULL, session_id TEXT,
 prompt_sha256 TEXT NOT NULL, response_sha256 TEXT NOT NULL,
 p_up REAL NOT NULL, expected_return_bps REAL NOT NULL,
 uncertainty REAL NOT NULL, action TEXT NOT NULL,
 tool_evidence TEXT NOT NULL, tool_evidence_provenance TEXT,
 completed_ms INTEGER NOT NULL,
 PRIMARY KEY(event_id, arm));
CREATE TABLE IF NOT EXISTS v22_outcomes(
 event_id TEXT NOT NULL, horizon_h INTEGER NOT NULL,
 due_ms INTEGER NOT NULL, exit_ms INTEGER, price REAL,
 drift_ms INTEGER, method TEXT, settled_ms INTEGER,
 PRIMARY KEY(event_id, horizon_h));
CREATE TABLE IF NOT EXISTS v22_attempts(
 event_id TEXT NOT NULL, attempt_id TEXT NOT NULL, arm TEXT NOT NULL,
 prompt_sha256 TEXT NOT NULL, state TEXT NOT NULL,
 prepared_ms INTEGER NOT NULL, started_ms INTEGER, finished_ms INTEGER,
 session_id TEXT, detail TEXT,
 PRIMARY KEY(event_id, attempt_id));
"""

# A triple is only sealable when all three arms answered with verified evidence.
ARM_ORDER = A.ARM_NAMES


def open_v22(path):
    con = sqlite3.connect(path, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(SCHEMA_V22)
    con.commit()
    return con


ATTEMPT_STATES = ("prepared", "started", "completed", "failed", "unknown")
TERMINAL_STATES = ("completed", "failed", "unknown")
ATTEMPT_LEASE_MS = 30 * 60 * 1000


def reserve_triple(con, *, event_id, prompts_sha, now_ms):
    """Write all three attempts as `prepared` BEFORE any invocation.

    Atomic: either all three rows exist or none do. A second reservation for the
    same event returns `already_reserved` and never rewrites the first intent,
    so a retry cannot erase the evidence that a call may have happened.
    """
    if sorted(prompts_sha) != sorted(ARM_ORDER):
        raise P.PrepError("incomplete_prompt_set")
    con.execute("BEGIN IMMEDIATE")
    try:
        existing = con.execute(
            "SELECT arm,attempt_id,state FROM v22_attempts WHERE event_id=?",
            (event_id,)).fetchall()
        if existing:
            con.rollback()
            return {"status": "already_reserved",
                    "attempts": [{"arm": a, "attempt_id": i, "state": st}
                                 for a, i, st in existing]}
        for arm in ARM_ORDER:
            attempt_id = hashlib.sha256(
                f"v22:{event_id}:{arm}".encode()).hexdigest()
            con.execute(
                "INSERT INTO v22_attempts(event_id,attempt_id,arm,prompt_sha256,"
                "state,prepared_ms) VALUES(?,?,?,?,'prepared',?)",
                (event_id, attempt_id, arm, prompts_sha[arm], int(now_ms)))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return {"status": "reserved",
            "attempts": [{"arm": arm,
                          "attempt_id": hashlib.sha256(
                              f"v22:{event_id}:{arm}".encode()).hexdigest()}
                         for arm in ARM_ORDER]}


def mark_arm(con, *, event_id, arm, state, now_ms=None, session_id=None,
             detail=None):
    """Monotone transition. A terminal row never moves again."""
    if state not in ATTEMPT_STATES:
        raise ValueError("invalid_attempt_state")
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT state FROM v22_attempts WHERE event_id=? "
                          "AND arm=?", (event_id, arm)).fetchone()
        if row is None:
            con.rollback()
            return False
        if row[0] in TERMINAL_STATES:
            con.rollback()
            return False
        con.execute(
            "UPDATE v22_attempts SET state=?, started_ms=COALESCE(started_ms,?)"
            ", finished_ms=?, session_id=COALESCE(?,session_id), "
            "detail=COALESCE(?,detail) WHERE event_id=? AND arm=?",
            (state, now_ms if state == "started" else None,
             now_ms if state in TERMINAL_STATES else None,
             session_id, detail, event_id, arm))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return True


def sweep_stale(con, *, now_ms, lease_ms=ATTEMPT_LEASE_MS):
    """Abandoned in-flight arms become `unknown`. They are never re-run."""
    rows = con.execute("SELECT event_id,arm,state FROM v22_attempts "
                       "WHERE state IN ('prepared','started') AND prepared_ms<=?",
                       (int(now_ms) - lease_ms,)).fetchall()
    for event_id, arm, state in rows:
        mark_arm(con, event_id=event_id, arm=arm, state="unknown",
                 now_ms=now_ms, detail="abandoned_in_" + state)
    return [{"event_id": e, "arm": a, "was": st} for e, a, st in rows]


def attempts_are_terminal(con, event_id):
    """True only when all three arms are durably terminal with full evidence."""
    rows = con.execute("SELECT arm,state FROM v22_attempts WHERE event_id=?",
                       (event_id,)).fetchall()
    if len(rows) != 3:
        return False, "attempts_incomplete"
    states = dict(rows)
    if sorted(states) != sorted(ARM_ORDER):
        return False, "attempt_arms_mismatch"
    for arm, state in states.items():
        if state not in TERMINAL_STATES:
            return False, "arm_not_terminal:" + arm + ":" + state
    return True, "ok"


def seal_triple(con, *, event, prepared, arm_results, quote, now_ms,
                synthetic=True):
    """Persist one three-arm record. Insert-only, never updated.

    Refuses rather than repairs: a missing arm, a duplicated arm, an unknown
    tool history or an entry quote taken before the last arm finished all raise
    a named error. There is no partial triple.
    """
    A.validate_three_arms(arm_results)
    event_id_check = event["event_id"]
    terminal, reason = attempts_are_terminal(con, event_id_check)
    if not terminal:
        # Evidence first: a triple with a still-live or missing attempt row is
        # not sealable, even if the caller hands over three plausible answers.
        raise P.PrepError("attempts_not_terminal:" + reason)
    P.validate_arm_contract([r for r in arm_results
                             if r["arm"] in (A.ARM_NEWS, A.ARM_NEWS_MARKET)],
                            quote)
    event_id = event["event_id"]
    if con.execute("SELECT 1 FROM v22_triples WHERE event_id=?",
                   (event_id,)).fetchone() is not None:
        return {"status": "already_sealed", "event_id": event_id}

    parsed = {}
    for r in arm_results:
        row = P.parse_arm_response(r["text"])
        parsed[r["arm"]] = {
            "attempt_id": r["attempt_id"], "session_id": r["session_id"],
            "p_up": row["p_up"],
            "expected_return_bps": row["expected_return_bps"],
            "uncertainty": row["uncertainty"],
            "action": P.decision(row["p_up"], row["expected_return_bps"]),
            "response_sha256": hashlib.sha256(r["text"].encode()).hexdigest(),
            "tool_evidence": r["tool_evidence"],
            "tool_evidence_provenance": r.get("tool_evidence_provenance"),
            "completed_ms": r["completed_ms"],
        }
    last_arm_ms = max(p["completed_ms"] for p in parsed.values())
    if int(quote["observed_ms"]) <= last_arm_ms:
        raise P.PrepError("entry_not_after_last_arm_completion")

    seal = hashlib.sha256(json.dumps({
        "protocol": A.PROTOCOL_VERSION,
        "event_id": event_id,
        "prompts": prepared["prompts_sha256"],
        "arms": sorted((arm, p["response_sha256"], p["completed_ms"])
                       for arm, p in parsed.items()),
        "entry": [quote["price"], quote["observed_ms"]],
        "cutoff_ms": prepared["cutoff_ms"],
        "synthetic": bool(synthetic)}, sort_keys=True).encode()).hexdigest()

    declared, effective = P.cohort_pair(event)
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "INSERT INTO v22_triples(event_id,protocol_version,prompt_version,"
            "prompt_hashes,cohort,declared_cohort,effective_cohort,"
            "cohort_provenance,source,published_ms,observed_ms,cutoff_ms,"
            "entry_price,entry_observed_ms,horizon_h,last_arm_ms,synthetic,"
            "seal_sha256,created_ms,state) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'sealed')",
            (event_id, A.PROTOCOL_VERSION, A.PROMPT_VERSION,
             json.dumps(prepared["prompts_sha256"], sort_keys=True),
             effective or declared, declared, effective,
             P.cohort_provenance(event), event["source"],
             event["published_ms"], event["observed_ms"],
             int(prepared["cutoff_ms"]), float(quote["price"]),
             int(quote["observed_ms"]), 4, last_arm_ms,
             1 if synthetic else 0, seal, int(now_ms)))
        for arm in ARM_ORDER:
            p = parsed[arm]
            con.execute(
                "INSERT INTO v22_arms(event_id,arm,attempt_id,session_id,"
                "prompt_sha256,response_sha256,p_up,expected_return_bps,"
                "uncertainty,action,tool_evidence,tool_evidence_provenance,"
                "completed_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, arm, p["attempt_id"], p["session_id"],
                 prepared["prompts_sha256"][arm], p["response_sha256"],
                 p["p_up"], p["expected_return_bps"], p["uncertainty"],
                 p["action"], p["tool_evidence"],
                 p["tool_evidence_provenance"], p["completed_ms"]))
        con.execute("INSERT INTO v22_outcomes(event_id,horizon_h,due_ms) "
                    "VALUES(?,?,?)",
                    (event_id, 4, int(quote["observed_ms"]) + 4 * 3_600_000))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return {"status": "already_sealed", "event_id": event_id}
    except BaseException:
        con.rollback()
        raise
    return {"status": "sealed", "event_id": event_id, "seal": seal,
            "arms": {arm: p["action"] for arm, p in parsed.items()}}


def mock_arm_results(event_id, actions, *, base_ms=1_000_000, tools=0):
    """Build three well-formed arm results without any model call.

    Used by the offline end-to-end tests and by the harness dry run. The text is
    a strict decision object, so the real parser validates it exactly as it
    would validate a model answer.
    """
    out = []
    for i, arm in enumerate(ARM_ORDER):
        p_up, bps, unc = actions[arm]
        out.append({
            "arm": arm,
            "attempt_id": hashlib.sha256(f"{event_id}:{arm}".encode()).hexdigest(),
            "session_id": f"mock-{arm}",
            "text": json.dumps({"p_up": p_up, "expected_return_bps": bps,
                                "uncertainty": unc, "rationale": "mock"},
                               sort_keys=True),
            "model": {"providerID": "deepseek", "modelID": "mock"},
            "session_terminal": "completed",
            "completed_ms": base_ms + i * 1000,
            "tool_evidence": "known_zero",
            "tool_evidence_provenance": "mock_harness",
            "tools": tools,
        })
    return out


def mock_quote(price=64000.0, observed_ms=1_010_000):
    return {"price": float(price), "observed_ms": int(observed_ms),
            "source": "binance_public_ticker"}


def run_offline_triple(path, *, event, market, actions, now_ms=2_000_000,
                       synthetic=True, fail_arm=None, quote_after_last=True,
                       entry_quote_ms=None):
    """Full offline pipeline: reserve -> mock arms -> entry quote -> seal.

    Every stage the live runner would go through is exercised, minus the model:
    the arms are reserved durably first, each transitions prepared -> started ->
    completed (or fails on demand), the entry quote is sampled only after the
    last arm, and the seal refuses anything incomplete.

    `fail_arm` simulates a crashed arm: that arm is left `failed` and the seal
    must refuse the triple rather than record two arms as if they were three.
    """
    prompts = A.build_prompts_3(event, market)
    prompts_sha = A.prompts_sha256(prompts)
    prepared = {"prompts_sha256": prompts_sha, "cutoff_ms": now_ms - 1}
    con = open_v22(path)
    try:
        reserved = reserve_triple(con, event_id=event["event_id"],
                                  prompts_sha=prompts_sha, now_ms=now_ms)
        if reserved["status"] != "reserved":
            return {"status": "already_reserved",
                    "attempts": reserved["attempts"]}, prompts

        results = mock_arm_results(event["event_id"], actions)
        for arm in ARM_ORDER:
            mark_arm(con, event_id=event["event_id"], arm=arm, state="started",
                     now_ms=now_ms + 100)
            if arm == fail_arm:
                mark_arm(con, event_id=event["event_id"], arm=arm,
                         state="failed", now_ms=now_ms + 200,
                         detail="mock_arm_crash")
                continue
            mark_arm(con, event_id=event["event_id"], arm=arm,
                     state="completed", now_ms=now_ms + 300,
                     session_id=f"mock-{arm}")

        if fail_arm is not None:
            return {"status": "refused",
                    "reason": "arm_failed:" + str(fail_arm)}, prompts

        last_arm_ms = max(r["completed_ms"] for r in results)
        if not quote_after_last:
            observed = entry_quote_ms or last_arm_ms + 1000
        else:
            observed = entry_quote_ms or max(last_arm_ms, 1_010_000) + 1000
        quote = mock_quote(observed_ms=observed)
        out = seal_triple(con, event=event, prepared=prepared,
                          arm_results=results, quote=quote,
                          now_ms=now_ms + 5000, synthetic=synthetic)
        return out, prompts
    finally:
        con.close()


def label_triple(con, event_id, *, due_ms, now_ms, candles):
    """Label a sealed triple with the SAME candle evidence rules as v2.1 (D15).

    This is not a decorative table: the label goes through
    `v21_pilot_cycle.pick_exit_candle`, so a partially formed candle, a span
    that is not one complete minute, a non-finite price or excessive drift are
    all refused here exactly as they are in the pilot lane. A refused label is
    recorded as a censored outcome with its reason, never as a number.
    """
    row = con.execute("SELECT exit_ms,settled_ms FROM v22_outcomes "
                      "WHERE event_id=? AND horizon_h=4", (event_id,)).fetchone()
    if row is None:
        raise P.PrepError("no_outcome_slot")
    if row[0] is not None:
        return {"status": "already_labelled", "event_id": event_id}
    import v21_pilot_cycle as PC
    try:
        picked = PC.pick_exit_candle(candles, int(due_ms), int(now_ms))
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        con.execute("UPDATE v22_outcomes SET method=?, settled_ms=? "
                    "WHERE event_id=? AND horizon_h=4 AND exit_ms IS NULL",
                    ("censored:" + reason, int(now_ms), event_id))
        con.commit()
        return {"status": "censored", "reason": reason, "event_id": event_id}
    con.execute("UPDATE v22_outcomes SET exit_ms=?, price=?, drift_ms=?, "
                "method=?, settled_ms=? WHERE event_id=? AND horizon_h=4 "
                "AND exit_ms IS NULL",
                (picked["exit_ms"], picked["price"], picked["drift_ms"],
                 picked["method"], int(now_ms), event_id))
    con.commit()
    return {"status": "labelled", "event_id": event_id, **picked}


def export_v22(con):
    """Append-only export of the three-arm ledger.

    Nothing is rewritten on re-export: the caller writes a new file with a new
    timestamp, exactly like the v2.1 exporter.
    """
    def rows(sql):
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    return {
        "kind": "news_v22_three_arm_export",
        "protocol_version": A.PROTOCOL_VERSION,
        "prompt_version": A.PROMPT_VERSION,
        "triples": rows("SELECT * FROM v22_triples ORDER BY created_ms"),
        "arms": rows("SELECT * FROM v22_arms ORDER BY event_id,arm"),
        "outcomes": rows("SELECT * FROM v22_outcomes ORDER BY event_id"),
        "attempts": rows("SELECT * FROM v22_attempts ORDER BY event_id,arm"),
        "limitations": {
            "labeler": "candle evidence reuses v21_pilot_cycle.pick_exit_candle "
                       "(D15); a refused label is exported as censored with its "
                       "reason, never as a price",
            "exactly_once": "NOT guaranteed end to end. The ledger is atomic per "
                            "transaction, but a model call is external: an "
                            "ambiguous result can only be recorded as unknown, "
                            "never replayed",
            "export": "read-only and append-only; re-export writes a new file",
        },
    }

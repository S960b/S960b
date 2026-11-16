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


def seal_triple(con, *, event, prepared, arm_results, quote, now_ms,
                synthetic=True):
    """Persist one three-arm record. Insert-only, never updated.

    Refuses rather than repairs: a missing arm, a duplicated arm, an unknown
    tool history or an entry quote taken before the last arm finished all raise
    a named error. There is no partial triple.
    """
    A.validate_three_arms(arm_results)
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
                       synthetic=True):
    """prepare -> mock arms -> seal, entirely offline. No model, no network."""
    prompts = A.build_prompts_3(event, market)
    prepared = {"prompts_sha256": A.prompts_sha256(prompts),
                "cutoff_ms": now_ms - 1}
    con = open_v22(path)
    try:
        results = mock_arm_results(event["event_id"], actions)
        out = seal_triple(con, event=event, prepared=prepared,
                          arm_results=results, quote=mock_quote(),
                          now_ms=now_ms, synthetic=synthetic)
        return out, prompts
    finally:
        con.close()

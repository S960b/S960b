#!/usr/bin/env python3
"""Independent NEWS v2.5 series budget and event claim ledger.

No model calls, feeds, network, timers or orders. The caller MUST successfully
reserve_model_call() (committed) before sending each request. Ambiguous failures
cost slots permanently. A separate approved activation controls execution.
"""
from __future__ import annotations
import json
import re
import sqlite3
import time
from pathlib import Path

ARM_NAMES = ("news", "news_market", "market")
SERIES_LIMIT = 100
CALL_LIMIT = 300
EVIDENCE_TIER = "observational_tier2"
SERIES_PURPOSE = "one_tier2_multi_event_observational"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS series_claims(
 event_id TEXT PRIMARY KEY,
 semantic_cluster_id TEXT NOT NULL UNIQUE,
 trial_id TEXT NOT NULL UNIQUE,
 claimed_ms INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('claimed','sealed','aborted','unknown')),
 final_reason TEXT, finalized_ms INTEGER
);
CREATE TABLE IF NOT EXISTS series_invocations(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL REFERENCES series_claims(event_id),
 arm TEXT NOT NULL CHECK(arm IN ('news','news_market','market')),
 reserved_ms INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','answered','unknown')),
 response_id TEXT,
 UNIQUE(event_id,arm)
);
CREATE TABLE IF NOT EXISTS series_labels(
 event_id TEXT PRIMARY KEY REFERENCES series_claims(event_id),
 state TEXT NOT NULL CHECK(state IN ('labelled','censored')),
 observed_ms INTEGER NOT NULL
);
"""

class Refused(RuntimeError):
    """Fail-closed condition. Never convert to approval or retry."""


def now_ms():
    return time.time_ns() // 1_000_000


def open_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise Refused("series_db_symlink")
    con = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def validate_activation(*, activation_path, gate_path, current_ms=None):
    """Read owner-staged limits. This module NEVER creates or alters the gate."""
    activation_path, gate_path = Path(activation_path), Path(gate_path)
    if (not gate_path.is_file() or gate_path.is_symlink()
            or not activation_path.is_file() or activation_path.is_symlink()):
        raise Refused("series_not_activated")
    try:
        data = json.loads(activation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("bad_activation_json") from exc
    if data.get("purpose") != SERIES_PURPOSE:
        raise Refused("wrong_series_purpose")
    if (data.get("evidence_tier") != EVIDENCE_TIER
            or data.get("upstream_tool_isolation") != "unverified"):
        raise Refused("evidence_claim_invalid")
    if data.get("paper_only") is not True:
        raise Refused("paper_only_required")
    if (data.get("max_model_invocations") != CALL_LIMIT
            or data.get("max_events") != SERIES_LIMIT):
        raise Refused("immutable_budget_invalid")
    if data.get("owner_go") is not True or not isinstance(data.get("owner_seq"), int):
        raise Refused("owner_go_missing")
    if not isinstance(data.get("series_id"), str) or not ID_RE.fullmatch(data["series_id"]):
        raise Refused("invalid_series_id")
    start, expires = data.get("started_ms"), data.get("expires_ms")
    if not isinstance(start, int) or not isinstance(expires, int) or expires <= start:
        raise Refused("invalid_activation_window")
    t = now_ms() if current_ms is None else int(current_ms)
    if not start <= t < expires:
        raise Refused("series_expired_or_not_started")
    return data


def _count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def counters(con):
    return {
        "claimed": _count(con, "series_claims"),
        "sealed": con.execute("SELECT COUNT(*) FROM series_claims WHERE state='sealed'").fetchone()[0],
        "labelled": con.execute("SELECT COUNT(*) FROM series_labels WHERE state='labelled'").fetchone()[0],
        "calls_reserved": _count(con, "series_invocations"),
        "aborted": con.execute("SELECT COUNT(*) FROM series_claims WHERE state='aborted'").fetchone()[0],
        "unknown": con.execute("SELECT COUNT(*) FROM series_claims WHERE state='unknown'").fetchone()[0]
    }


def claim(con, *, event_id, semantic_cluster_id, trial_id, at_ms):
    """Atomic at-most-once claim of the news item AND semantic news cluster."""
    if not all(isinstance(v, str) and ID_RE.fullmatch(v)
               for v in (event_id, semantic_cluster_id, trial_id)):
        raise Refused("invalid_claim_identifier")
    con.execute("BEGIN IMMEDIATE")
    try:
        c = counters(con)
        if c["sealed"] >= SERIES_LIMIT or c["calls_reserved"] >= CALL_LIMIT:
            raise Refused("series_budget_exhausted")
        con.execute(
            "INSERT INTO series_claims(event_id,semantic_cluster_id,trial_id,claimed_ms,state)"
            " VALUES(?,?,?,?,'claimed')",
            (event_id, semantic_cluster_id, trial_id, int(at_ms)))
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise Refused("duplicate_event_or_cluster_or_trial") from exc
    except BaseException:
        con.rollback()
        raise
    return trial_id


def reserve_model_call(con, *, event_id, arm, at_ms):
    """Durable global budget slot committed BEFORE any request is transmitted."""
    if arm not in ARM_NAMES:
        raise Refused("unknown_arm")
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT state FROM series_claims WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row[0] != "claimed":
            raise Refused("event_not_claimed_or_terminal")
        c = counters(con)
        if c["calls_reserved"] >= CALL_LIMIT or c["sealed"] >= SERIES_LIMIT:
            raise Refused("series_model_budget_exhausted")
        idx = ARM_NAMES.index(arm)
        done = con.execute(
            "SELECT arm,state FROM series_invocations WHERE event_id=? ORDER BY id", (event_id,)
        ).fetchall()
        if len(done) != idx or any(x[0] != ARM_NAMES[i] or x[1] != "answered"
                                    for i, x in enumerate(done)):
            raise Refused("arm_order_or_previous_response_invalid")
        con.execute(
            "INSERT INTO series_invocations(event_id,arm,reserved_ms,state)"
            " VALUES(?,?,?,'reserved')", (event_id, arm, int(at_ms)))
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise Refused("duplicate_model_invocation") from exc
    except BaseException:
        con.rollback()
        raise
    return c["calls_reserved"] + 1


def record_response(con, *, event_id, arm, response_id):
    """Record real provider body id, or explicit unavailable, never fabricate."""
    if response_id is None:
        response_id = "unavailable:no_provider_id"
    if not isinstance(response_id, str) or not response_id or len(response_id) > 256:
        raise Refused("invalid_response_id")
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute(
            "UPDATE series_invocations SET state='answered',response_id=? "
            "WHERE event_id=? AND arm=? AND state='reserved'",
            (response_id, event_id, arm))
        if row.rowcount != 1:
            raise Refused("response_not_reserved_or_already_final")
        con.commit()
    except BaseException:
        con.rollback()
        raise


def finalize(con, *, event_id, state, reason=None, at_ms=None):
    if state not in ("sealed", "aborted", "unknown"):
        raise Refused("invalid_terminal_state")
    con.execute("BEGIN IMMEDIATE")
    try:
        if state == "sealed":
            rows = con.execute(
                "SELECT arm,state FROM series_invocations WHERE event_id=? ORDER BY id",
                (event_id,)).fetchall()
            if rows != [(arm, "answered") for arm in ARM_NAMES]:
                raise Refused("cannot_seal_incomplete_triple")
        row = con.execute(
            "UPDATE series_claims SET state=?,final_reason=?,finalized_ms=? "
            "WHERE event_id=? AND state='claimed'",
            (state, reason, now_ms() if at_ms is None else int(at_ms), event_id))
        if row.rowcount != 1:
            raise Refused("claim_already_terminal_or_missing")
        con.commit()
    except BaseException:
        con.rollback()
        raise


def record_label(con, *, event_id, state, at_ms):
    if state not in ("labelled", "censored"):
        raise Refused("invalid_label_state")
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT state FROM series_claims WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row[0] != "sealed":
            raise Refused("cannot_label_unsealed")
        con.execute(
            "INSERT INTO series_labels(event_id,state,observed_ms) VALUES(?,?,?)",
            (event_id, state, int(at_ms)))
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise Refused("label_already_recorded") from exc
    except BaseException:
        con.rollback()
        raise

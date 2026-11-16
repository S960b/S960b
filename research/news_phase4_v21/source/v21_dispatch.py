#!/usr/bin/env python3
"""NEWS v2.1 — dry-run dispatch queue with persistent atomic quotas.

PAPER ONLY. This module never calls a model. It decides WHICH verified fresh
event would be dispatched to a (stub) model interface, enforces the 4-call/day
and 2-paired-events/day limits atomically in SQLite, and guarantees idempotence
and crash recovery.

Design rules enforced here:
  * only events with fast_eligible = 1 may enter the dispatch queue;
  * synthetic/fixture events are marked and can never reach production metrics;
  * quotas are taken inside BEGIN IMMEDIATE, so two concurrent runs cannot
    over-spend;
  * an attempt that has a reservation row is never re-issued (no replay).
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

POLICY = "news_v21_free_sources_20261115"
# The owner approved ONE shared budget for all NEWS model calls. Legacy v2 owns
# the same gateway and used to keep its own independent cap of 4/day, which
# would have doubled real spend. Its model dispatch is now disabled in the
# plugin config, and this module additionally counts any attempts it still makes
# so the combined ceiling can never be exceeded.
LEGACY_V2_DB = Path.home() / ".local/share/news-phase4-v2/v2.sqlite"
# The daily ceiling is OUR research policy, not a provider limit. The owner has
# unlimited DeepSeek access through the gateway; these numbers exist only to
# stop a runaway loop from generating a request storm. They are read from the
# environment so a pilot can be widened without a code change, and default to a
# deliberately small value.
def _env_int(name, default):
    try:
        value = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_CALLS_PER_DAY = _env_int("NEWS_V21_MAX_CALLS_PER_DAY", 20)
MAX_PAIRS_PER_DAY = _env_int("NEWS_V21_MAX_PAIRS_PER_DAY", 10)
# Concurrency is one pair at a time; a lease in dispatch_lease replaces the old
# fixed cooldown, which choked sample collection as a workaround for the five
# plugin timers.
PAIR_LEASE_MS = _env_int("NEWS_V21_PAIR_LEASE_MS", 5) * 60_000
# Kept for compatibility with already-written callers and tests.
PAIR_COOLDOWN_MS = 0
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_queue(
 attempt_id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL,
 arm TEXT NOT NULL CHECK(arm IN ('news','news_market')),
 created_ms INTEGER NOT NULL,
 synthetic INTEGER NOT NULL DEFAULT 0 CHECK(synthetic IN (0,1)),
 UNIQUE(event_id, arm, synthetic));
CREATE TABLE IF NOT EXISTS dispatch_reservations(
 attempt_id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL,
 arm TEXT NOT NULL,
 reserved_ms INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','completed','abandoned')),
 synthetic INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS dispatch_log(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 attempt_id TEXT NOT NULL,
 event_id TEXT NOT NULL,
 arm TEXT NOT NULL,
 action TEXT NOT NULL,
 at_ms INTEGER NOT NULL,
 synthetic INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_res_day ON dispatch_reservations(reserved_ms);
CREATE TABLE IF NOT EXISTS dispatch_lease(
 id INTEGER PRIMARY KEY CHECK(id = 1),
 holder TEXT NOT NULL,
 acquired_ms INTEGER NOT NULL,
 expires_ms INTEGER NOT NULL,
 synthetic INTEGER NOT NULL DEFAULT 0);
"""


class DispatchError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise DispatchError(code)


def clock_ms():
    return time.time_ns() // 1_000_000


def open_db(path, *, create=False):
    p = Path(path).expanduser()
    if create:
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(p), timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    _migrate(con)
    con.executescript(SCHEMA)
    con.commit()
    return con


def _migrate(con):
    """Rebuild dispatch_queue when it still carries the old two-column key.

    Fixtures and production share event_id, so UNIQUE(event_id, arm) let a
    synthetic run silently occupy the production slot.
    """
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                      "AND name='dispatch_queue'").fetchone()
    if not row or "UNIQUE(event_id, arm, synthetic)" in (row[0] or ""):
        return
    con.executescript("""
      ALTER TABLE dispatch_queue RENAME TO dispatch_queue_old;
    """ + SCHEMA.split("CREATE TABLE IF NOT EXISTS dispatch_log")[0]
        .replace("dispatch_queue(", "dispatch_queue(")
        + """
      INSERT OR IGNORE INTO dispatch_queue
        SELECT attempt_id,event_id,arm,created_ms,synthetic FROM dispatch_queue_old;
      DROP TABLE dispatch_queue_old;
    """)
    con.commit()


def _day_bounds(now_ms):
    day = now_ms // DAY_MS * DAY_MS
    return day, day + DAY_MS


def _legacy_calls_today(now_ms):
    """Model attempts the legacy v2 cohort recorded today (read-only)."""
    try:
        if not LEGACY_V2_DB.is_file():
            # Fail CLOSED. A missing counter means "unknown", never "zero":
            # assuming zero is exactly how two independent quotas reappear.
            return MAX_CALLS_PER_DAY
        if LEGACY_V2_DB.stat().st_size < 4096:
            return MAX_CALLS_PER_DAY
        lcon = sqlite3.connect(f"file:{LEGACY_V2_DB}?mode=ro", uri=True)
        try:
            lcon.execute("PRAGMA query_only=ON")
            day = now_ms // DAY_MS * DAY_MS
            row = lcon.execute(
                "SELECT COUNT(*) FROM attempts WHERE reserved_ms>=? AND reserved_ms<?",
                (day, day + DAY_MS)).fetchone()
            if row is None:
                return MAX_CALLS_PER_DAY
            return int(row[0])
        finally:
            lcon.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        # Fail CLOSED: if the legacy counter cannot be read, assume it consumed
        # the budget rather than risk double spend.
        return MAX_CALLS_PER_DAY


def combined_calls_today(con, now_ms, synthetic):
    calls, pairs = _counts(con, now_ms, synthetic)
    if synthetic:
        return calls, pairs
    return calls + _legacy_calls_today(now_ms), pairs


def _counts(con, now_ms, synthetic):
    day, end = _day_bounds(now_ms)
    calls = con.execute(
        "SELECT COUNT(*) FROM dispatch_reservations "
        "WHERE reserved_ms>=? AND reserved_ms<? AND synthetic=?",
        (day, end, synthetic)).fetchone()[0]
    pairs = con.execute(
        "SELECT COUNT(DISTINCT event_id) FROM dispatch_reservations "
        "WHERE reserved_ms>=? AND reserved_ms<? AND synthetic=?",
        (day, end, synthetic)).fetchone()[0]
    return calls, pairs


def enqueue(con, event, *, synthetic=False, now_ms=None):
    """Queue both arms for one verified fast event. Idempotent."""
    now_ms = int(now_ms if now_ms is not None else clock_ms())
    eid = event.get("event_id")
    require(isinstance(eid, str) and len(eid) == 64, "invalid_event_id")
    bucket = 1 if synthetic else 0
    con.execute("BEGIN IMMEDIATE")
    try:
        created = 0
        for arm in ("news", "news_market"):
            # Fixture rows carry their own id namespace so a synthetic run can
            # never occupy, block or masquerade as a production attempt.
            ns = "v21fx" if synthetic else "v21"
            aid = hashlib.sha256(f"{ns}:{eid}:{arm}".encode()).hexdigest()
            cur = con.execute(
                "INSERT OR IGNORE INTO dispatch_queue"
                "(attempt_id,event_id,arm,created_ms,synthetic) VALUES(?,?,?,?,?)",
                (aid, eid, arm, now_ms, bucket))
            created += cur.rowcount
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return {"queued": created, "event_id": eid, "synthetic": bool(synthetic)}


def reserve_pair(con, event_id, *, synthetic=False, now_ms=None):
    """Atomically take 2 model-call reservations for one event.

    Returns {'status': 'ready'|'skip', ...}. Never issues a model call.
    """
    now_ms = int(now_ms if now_ms is not None else clock_ms())
    con.execute("BEGIN IMMEDIATE")
    try:
        bucket = 1 if synthetic else 0
        already = con.execute(
            "SELECT COUNT(*) FROM dispatch_reservations "
            "WHERE event_id=? AND synthetic=?", (event_id, bucket)).fetchone()[0]
        if already:
            con.rollback()
            return {"status": "skip", "reason": "already_reserved_no_replay"}
        calls, pairs = combined_calls_today(con, now_ms,
                                            1 if synthetic else 0)
        if calls + 2 > MAX_CALLS_PER_DAY:
            con.rollback()
            return {"status": "skip", "reason": "daily_call_cap"}
        if pairs >= MAX_PAIRS_PER_DAY:
            con.rollback()
            return {"status": "skip", "reason": "daily_pair_cap"}
        # One pair at a time: a lease, not a fixed cooldown. It expires by
        # itself, so a crashed dispatch cannot wedge the lane forever.
        lease = con.execute(
            "SELECT holder,expires_ms,synthetic FROM dispatch_lease WHERE id=1"
        ).fetchone()
        if lease is not None and int(lease[1]) > now_ms:
            if int(lease[2]) == bucket:
                con.rollback()
                return {"status": "skip", "reason": "pair_lease_held",
                        "lease_until_ms": int(lease[1])}
            # A synthetic lease must never block production and vice versa.
            con.execute("DELETE FROM dispatch_lease WHERE id=1")
        arms = con.execute(
            "SELECT attempt_id,arm FROM dispatch_queue "
            "WHERE event_id=? AND synthetic=? ORDER BY arm",
            (event_id, bucket)).fetchall()
        if len(arms) != 2:
            con.rollback()
            return {"status": "skip", "reason": "pair_not_queued"}
        for aid, arm in arms:
            con.execute(
                "INSERT INTO dispatch_reservations"
                "(attempt_id,event_id,arm,reserved_ms,state,synthetic)"
                " VALUES(?,?,?,?,'reserved',?)",
                (aid, event_id, arm, now_ms, 1 if synthetic else 0))
        con.execute(
            "INSERT INTO dispatch_lease(id,holder,acquired_ms,expires_ms,"
            "synthetic) VALUES(1,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET holder=excluded.holder,"
            "acquired_ms=excluded.acquired_ms,expires_ms=excluded.expires_ms,"
            "synthetic=excluded.synthetic",
            (event_id, now_ms, now_ms + PAIR_LEASE_MS, bucket))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    calls, pairs = combined_calls_today(con, now_ms, 1 if synthetic else 0)
    return {"status": "ready", "event_id": event_id,
            "attempts": [a for a, _ in arms],
            "calls_today": calls, "pairs_today": pairs,
            "synthetic": bool(synthetic)}


def stub_model(attempt):
    """Stand-in for the real model interface. Performs NO network call."""
    return {"status": "stubbed", "attempt_id": attempt,
            "would_call": POLICY, "network": False}


def run_dry(con, event, *, synthetic=True, now_ms=None):
    q = enqueue(con, event, synthetic=synthetic, now_ms=now_ms)
    r = reserve_pair(con, event["event_id"], synthetic=synthetic, now_ms=now_ms)
    if r.get("status") != "ready":
        return {"enqueue": q, "reserve": r, "model_calls": []}
    results = [stub_model(a) for a in r["attempts"]]
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "UPDATE dispatch_reservations SET state='completed' "
            "WHERE event_id=?", (event["event_id"],))
        for a in r["attempts"]:
            con.execute("INSERT INTO dispatch_log"
                        "(attempt_id,event_id,arm,action,at_ms,synthetic)"
                        " VALUES(?,?,?,'dry_run_stub',?,?)",
                        (a, event["event_id"], "news", 
                         int(now_ms or clock_ms()), 1 if synthetic else 0))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return {"enqueue": q, "reserve": r, "model_calls": results}


def status(con, now_ms=None):
    now_ms = int(now_ms if now_ms is not None else clock_ms())
    out = {"policy": POLICY, "max_calls_per_day": MAX_CALLS_PER_DAY,
           "max_pairs_per_day": MAX_PAIRS_PER_DAY,
           "pair_lease_minutes": PAIR_LEASE_MS // 60_000,
           "queue": con.execute("SELECT COUNT(*) FROM dispatch_queue")
                    .fetchone()[0],
           "reservations": con.execute(
               "SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0]}
    for label, flag in (("production", 0), ("synthetic", 1)):
        calls, pairs = combined_calls_today(con, now_ms, flag)
        out[label] = {"calls_today": calls, "pairs_today": pairs}
    out["legacy_v2_calls_today"] = _legacy_calls_today(now_ms)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("status", "dry-run"))
    p.add_argument("--db", required=True)
    p.add_argument("--synthetic", action="store_true")
    a = p.parse_args(argv)
    try:
        con = open_db(a.db, create=True)
        if a.command == "status":
            print(json.dumps(status(con), sort_keys=True))
            return 0
        event = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(run_dry(con, event, synthetic=a.synthetic),
                         sort_keys=True))
        return 0
    except (DispatchError, sqlite3.Error, ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "code": str(exc)[:80]}))
        return 2
    finally:
        try:
            con.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

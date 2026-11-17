#!/usr/bin/env python3
"""NEWS Tier-2 — bounded +4h label watcher (PAPER only, dry by default).

Stage-28 gap: nothing settled a sealed triple at entry + 4 h. `driver.label_once`
exists but is manual, and — measured, not assumed — it calls `P.resolve_exit`
BEFORE `TR.label_once`, so a exchange outage that fails inside `resolve_exit`
never reaches the retry counter at all (three failures in a row leave
`tier2_label_attempts` at 0). A sealed row could then sit unlabelled forever
while every run reported "pending".

This module settles rows and **counts every failure in one place**, so the
bounded-censor rule has something to count. It:

  * operates ONLY on rows already present in `tier2_triples`;
  * derives the due moment from the immutable `entry_observed_ms + horizon_ms`,
    never from a caller and never from a digest;
  * never imports a model client and never invokes inference;
  * never touches the feed database, the collector, legacy stores or flags;
  * is dry by default: dry mode writes nothing at all, not even attempts;
  * is one-shot: no daemon, no cron, no sleep loop. Run it as often as you like;
    it is idempotent because terminal outcomes are written once.

Failure policy, in one place:

    fetch/validation failure -> attempt counter +1, state pending
    attempts >= MAX or now > due + MAX_WAIT -> censored, price 0.0
    anything else unexpected -> status=error, exit 3, NOT counted as transient
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import tier2 as T2
import tier2_trigger as TR

MAX_FAILURES = 3
MAX_WAIT_MS = 6 * 3_600_000
EXIT_OK = 0
EXIT_ERROR = 3
EXIT_REFUSED = 4


class LabelWatchError(RuntimeError):
    pass


def _connect(db_path):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise LabelWatchError("no_tier2_db")
    return sqlite3.connect(str(db_path), timeout=10)


def sealed_rows(con):
    """Every sealed triple, with the due moment derived from immutable fields."""
    rows = con.execute(
        "SELECT event_id, entry_observed_ms, horizon_ms FROM tier2_triples "
        "ORDER BY created_ms ASC").fetchall()
    out = []
    for event_id, entry_ms, horizon_ms in rows:
        entry_ms, horizon_ms = int(entry_ms), int(horizon_ms)
        if entry_ms <= 0 or horizon_ms <= 0:
            raise LabelWatchError("sealed_entry_or_horizon_invalid")
        out.append({"event_id": event_id, "entry_observed_ms": entry_ms,
                    "horizon_ms": horizon_ms, "due_ms": entry_ms + horizon_ms})
    return out


def existing_outcome(con, event_id):
    row = con.execute("SELECT label_state FROM tier2_outcomes WHERE event_id=?",
                      (event_id,)).fetchone()
    return None if row is None else row[0]


def _record_attempt(con, event_id, *, now_ms, error, state="pending"):
    """One place where a failure becomes a counted attempt."""
    con.executescript(TR.LABEL_ATTEMPTS_SCHEMA)
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT attempts FROM tier2_label_attempts WHERE "
                          "event_id=?", (event_id,)).fetchone()
        attempts = (row[0] if row else 0) + 1
        con.execute(
            "INSERT OR REPLACE INTO tier2_label_attempts "
            "(event_id,attempts,first_failure_ms,last_failure_ms,last_error,"
            "state) VALUES(?,?,COALESCE((SELECT first_failure_ms FROM "
            "tier2_label_attempts WHERE event_id=?),?),?,?,?)",
            (event_id, attempts, event_id, int(now_ms), int(now_ms),
             str(error)[:120], state))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return attempts


def _write_censored(con, event_id, due_ms, now_ms, reason):
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "INSERT OR IGNORE INTO tier2_outcomes(event_id,due_ms,price,"
            "observed_ms,source,drift_ms,label_state) VALUES(?,?,?,?,?,?,?)",
            (event_id, int(due_ms), 0.0, int(now_ms), "unavailable", -1,
             "censored"))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"event_id": event_id, "status": "censored", "reason": reason}


def settle_one(con, row, *, now_ms, candle_fn, dry, max_failures=MAX_FAILURES,
               max_wait_ms=MAX_WAIT_MS):
    """Settle one sealed triple. Returns a status dict; writes only if not dry."""
    event_id, due = row["event_id"], row["due_ms"]

    state = existing_outcome(con, event_id)
    if state is not None:
        return {"event_id": event_id, "status": state, "already": True}

    if int(now_ms) < due:
        return {"event_id": event_id, "status": "pending",
                "reason": "not_due_yet", "due_ms": due}

    if dry:
        # Dry mode decides what it WOULD do and writes nothing at all.
        try:
            candle = candle_fn(due_ms=due)
            T2.validate_candle(candle, due, symbol=TR.LABEL_SYMBOL)
        except Exception as exc:
            return {"event_id": event_id, "status": "would_pending",
                    "reason": type(exc).__name__}
        return {"event_id": event_id, "status": "would_label",
                "price": candle["close"]}

    try:
        candle = candle_fn(due_ms=due)
        T2.validate_candle(candle, due, symbol=TR.LABEL_SYMBOL)
    except Exception as exc:
        attempts = _record_attempt(con, event_id, now_ms=now_ms, error=exc)
        waited = int(now_ms) - due
        if attempts >= max_failures or waited > max_wait_ms:
            return _write_censored(con, event_id, due, now_ms,
                                   type(exc).__name__)
        return {"event_id": event_id, "status": "pending",
                "attempts": attempts, "reason": type(exc).__name__}

    drift = int(candle["close_ms"]) - due
    con.execute("BEGIN IMMEDIATE")
    try:
        if existing_outcome(con, event_id) is not None:
            con.rollback()
            return {"event_id": event_id,
                    "status": existing_outcome(con, event_id),
                    "already": True}
        con.execute("INSERT INTO tier2_outcomes(event_id,due_ms,price,"
                    "observed_ms,source,drift_ms,label_state) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (event_id, due, float(candle["close"]),
                     int(candle["close_ms"]), "binance_public_1m_close",
                     drift, "labelled"))
        # The attempts table exists only after a failure was counted; on a
        # clean first pass there is nothing to clear.
        con.execute("CREATE TABLE IF NOT EXISTS tier2_label_attempts("
                    "event_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL,"
                    "first_failure_ms INTEGER, last_failure_ms INTEGER,"
                    "last_error TEXT, state TEXT NOT NULL)")
        con.execute("DELETE FROM tier2_label_attempts WHERE event_id=?",
                    (event_id,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"event_id": event_id, "status": "labelled", "price": candle["close"],
            "drift_ms": drift}


def settle(*, db_path, now_ms, candle_fn, dry=True, **limits):
    """One bounded pass over all sealed triples. No loop, no sleep, no daemon."""
    con = _connect(db_path)
    try:
        rows = sealed_rows(con)
        if not rows:
            return {"status": "no_triples", "outcomes": []}
        outcomes = [settle_one(con, r, now_ms=now_ms, candle_fn=candle_fn,
                               dry=dry, **limits) for r in rows]
        overall = "error" if any(o["status"] == "error" for o in outcomes) else "checked"
        return {"status": overall, "dry": bool(dry), "outcomes": outcomes}
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(T2.DEFAULT_DB))
    ap.add_argument("--settle", action="store_true",
                    help="write outcomes; without it nothing is written")
    ap.add_argument("--now-ms", type=int, default=None)
    args = ap.parse_args(argv)
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
    try:
        import v21_paper_prep as P

        def candle_fn(*, due_ms):
            exit_data = P.resolve_exit(due_ms, now_ms)
            close_ms = int(exit_data["exit_ms"])
            return {"symbol": TR.LABEL_SYMBOL, "open_ms": close_ms - 59_999,
                    "close_ms": close_ms, "closed": close_ms < now_ms,
                    "close": float(exit_data["price"])}

        out = settle(db_path=args.db, now_ms=now_ms, candle_fn=candle_fn,
                     dry=not args.settle)
    except LabelWatchError as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)},
                         sort_keys=True))
        return EXIT_REFUSED
    except Exception as exc:
        print(json.dumps({"status": "error", "reason": type(exc).__name__,
                          "detail": str(exc)[:200]}, sort_keys=True))
        return EXIT_ERROR
    print(json.dumps(out, sort_keys=True))
    return EXIT_OK if out["status"] in ("checked", "no_triples") else EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

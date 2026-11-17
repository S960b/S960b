#!/usr/bin/env python3
"""NEWS Tier-2 — bounded +4h label watcher (PAPER only, dry by default).

Stage-29 rewrite. Three defects from the previous version, each now closed:

1. **No synthesized candle.** The old `main()` invented `open_ms = exit_ms-59999`,
   `close_ms = exit_ms`, `closed = exit_ms < now`. Those are not observations of
   an exchange candle; they are a reshaped dict that happens to satisfy a
   validator. This module takes the candle from the exchange payload itself and
   refuses to settle when the payload cannot support the claim.

2. **Atomic check → attempt → write.** The old `_record_attempt` ran
   `executescript` outside `BEGIN IMMEDIATE`, so two workers could both count an
   attempt, or one could censor after another had already labelled. Every state
   change now happens under one `BEGIN IMMEDIATE` with the current state read
   inside the same transaction.

3. **Transient vs bug.** A bare `except Exception` around the candle path turned
   programming errors into "exchange outage" and could censor a row because of a
   TypeError. Only known transport/validation failures count as attempts; anything
   else is an `error`, visible, and never censors.

Dry mode writes nothing at all — not even the attempts table.
"""
import argparse
import json
import sqlite3
import time
from pathlib import Path

import tier2 as T2
import tier2_trigger as TR
import tier2_integrity as INTEG

MAX_FAILURES = 3
MAX_WAIT_MS = 6 * 3_600_000
EXIT_OK = 0
EXIT_ERROR = 3
EXIT_REFUSED = 4

# Failures that mean "the source could not give us a candle yet". Anything not
# in this set is a bug and must not be allowed to censor a row.
TRANSIENT_MARKERS = (
    "label_fetch_failed",       # transport / timeout / DNS
    "label_fetch_invalid_json",
    "label_fetch_invalid_rows",
    "label_pending_candle_open",
    "label_candle_unavailable",
    "label_row_invalid",
    "label_drift_too_large",
    "label_price_invalid",
    "candle_missing",
    "candle_not_closed",
    "candle_open_not_aligned",
    "candle_span_invalid",
    "candle_price_not_positive",
    "candle_price_not_numeric",
    "candle_price_not_finite",
    "candle_drift_out_of_range",
    "candle_symbol_mismatch",
)
# Socket-level exceptions that are unambiguously transport.
TRANSIENT_TYPES = (ConnectionError, TimeoutError, OSError)


class LabelWatchError(RuntimeError):
    pass


def classify(exc):
    """'transient' or 'bug'. Bugs must stay visible and must never censor."""
    text = str(exc)
    for marker in TRANSIENT_MARKERS:
        if marker in text:
            return "transient"
    if isinstance(exc, TRANSIENT_TYPES):
        return "transient"
    return "bug"


def _connect(db_path):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise LabelWatchError("no_tier2_db")
    return sqlite3.connect(str(db_path), timeout=10)


def sealed_rows(con):
    rows = con.execute(
        "SELECT event_id, entry_observed_ms, horizon_ms FROM tier2_triples "
        "ORDER BY created_ms ASC").fetchall()
    out = []
    for event_id, entry_ms, horizon_ms in rows:
        # A sealed-looking DB row is NOT evidence of validity. Verify the
        # canonical full-arm digest, raw bytes and attestation chain BEFORE
        # any exchange call or irreversible +4h label write.
        try:
            INTEG.verify_binding(con, event_id)
        except INTEG.IntegrityError as exc:
            raise LabelWatchError("sealed_evidence_invalid:" + str(exc)) from None
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


def ensure_attempts(con):
    con.executescript(TR.LABEL_ATTEMPTS_SCHEMA)
    con.commit()


def _attempts_table_exists(con):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='tier2_label_attempts'").fetchone() is not None


def _attempts_of(con, event_id):
    if not _attempts_table_exists(con):
        return 0, "pending"
    row = con.execute("SELECT attempts, state FROM tier2_label_attempts "
                      "WHERE event_id=?", (event_id,)).fetchone()
    return (0, "pending") if row is None else (int(row[0]), row[1])


def _tx(con, body):
    """Run body inside one BEGIN IMMEDIATE. Rollback on any exception."""
    con.execute("BEGIN IMMEDIATE")
    try:
        out = body()
        con.commit()
        return out
    except Exception:
        con.rollback()
        raise


def _count_failure(con, event_id, *, now_ms, error, due_ms, max_failures,
                   max_wait_ms):
    """Count one transient failure and censor if the bound is reached.

    Read, increment and the possible censoring write happen inside ONE
    transaction, so a concurrent success cannot be overwritten by a censor and a
    concurrent censor cannot be double-counted.
    """
    def body():
        if existing_outcome(con, event_id) is not None:
            return {"event_id": event_id,
                    "status": existing_outcome(con, event_id), "already": True}
        attempts, _state = _attempts_of(con, event_id)
        attempts += 1
        exhausted = attempts >= max_failures or (int(now_ms) - int(due_ms)) > max_wait_ms
        if exhausted:
            con.execute(
                "INSERT OR IGNORE INTO tier2_outcomes(event_id,due_ms,price,"
                "observed_ms,source,drift_ms,label_state) VALUES(?,?,?,?,?,?,?)",
                (event_id, int(due_ms), 0.0, int(now_ms), "unavailable", -1,
                 "censored"))
            con.execute(
                "INSERT OR REPLACE INTO tier2_label_attempts "
                "(event_id,attempts,first_failure_ms,last_failure_ms,last_error,"
                "state) VALUES(?,?,COALESCE((SELECT first_failure_ms FROM "
                "tier2_label_attempts WHERE event_id=?),?),?,?,?)",
                (event_id, attempts, event_id, int(now_ms), int(now_ms),
                 str(error)[:120], "censored"))
            return {"event_id": event_id, "status": "censored",
                    "attempts": attempts, "reason": str(error)[:80]}
        con.execute(
            "INSERT OR REPLACE INTO tier2_label_attempts "
            "(event_id,attempts,first_failure_ms,last_failure_ms,last_error,"
            "state) VALUES(?,?,COALESCE((SELECT first_failure_ms FROM "
            "tier2_label_attempts WHERE event_id=?),?),?,?,?)",
            (event_id, attempts, event_id, int(now_ms), int(now_ms),
             str(error)[:120], "pending"))
        return {"event_id": event_id, "status": "pending",
                "attempts": attempts, "reason": str(error)[:80]}

    return _tx(con, body)


def _write_label(con, event_id, *, due_ms, candle):
    drift = int(candle["close_ms"]) - int(due_ms)

    def body():
        current = existing_outcome(con, event_id)
        if current is not None:
            return {"event_id": event_id, "status": current, "already": True}
        con.execute("INSERT INTO tier2_outcomes(event_id,due_ms,price,"
                    "observed_ms,source,drift_ms,label_state) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (event_id, int(due_ms), float(candle["close"]),
                     int(candle["close_ms"]), str(candle["source"]),
                     drift, "labelled"))
        if _attempts_table_exists(con):
            con.execute("DELETE FROM tier2_label_attempts WHERE event_id=?",
                        (event_id,))
        return {"event_id": event_id, "status": "labelled",
                "price": candle["close"], "drift_ms": drift}

    return _tx(con, body)


def settle_one(con, row, *, now_ms, candle_fn, dry, max_failures=MAX_FAILURES,
               max_wait_ms=MAX_WAIT_MS):
    event_id, due = row["event_id"], row["due_ms"]

    state = existing_outcome(con, event_id)
    if state is not None:
        return {"event_id": event_id, "status": state, "already": True}
    if int(now_ms) < due:
        return {"event_id": event_id, "status": "pending",
                "reason": "not_due_yet", "due_ms": due}

    # F06: the target Binance candle cannot be closed until the end of the
    # minute containing due_ms. Polling sooner is NOT an exchange failure and
    # must not consume the bounded three-failure budget. Binance kline close
    # timestamps are minute_open + 59_999 milliseconds.
    first_close_ms = (int(due) // 60_000) * 60_000 + 59_999
    if int(now_ms) <= first_close_ms:
        return {"event_id": event_id, "status": "pending",
                "reason": "await_first_closed_candle",
                "due_ms": due, "first_close_ms": first_close_ms}

    if dry:
        # Dry decides what it WOULD do. No writes, no schema creation.
        try:
            candle = candle_fn(due_ms=due)
            T2.validate_candle(candle, due, symbol=TR.LABEL_SYMBOL)
        except Exception as exc:
            return {"event_id": event_id, "status": "would_pending",
                    "reason": str(exc)[:80], "kind": classify(exc)}
        return {"event_id": event_id, "status": "would_label",
                "price": candle["close"], "source": candle.get("source")}

    try:
        candle = candle_fn(due_ms=due)
        T2.validate_candle(candle, due, symbol=TR.LABEL_SYMBOL)
    except Exception as exc:
        # F06: a correctly timed request can still find that the exchange
        # has not published the first closed candle. Waiting for publication
        # does not spend failure attempts. After the hard max-wait deadline,
        # retain the existing censoring rule instead of waiting forever.
        if ("label_pending_candle_open" in str(exc)
                or "candle_not_closed" in str(exc)):
            if int(now_ms) - int(due) <= max_wait_ms:
                return {"event_id": event_id, "status": "pending",
                        "reason": "await_exchange_candle_close",
                        "due_ms": due}
        if classify(exc) != "transient":
            # A bug is not an outage. Surface it and leave the row untouched.
            return {"event_id": event_id, "status": "error",
                    "reason": type(exc).__name__ + ":" + str(exc)[:160]}
        if not _attempts_table_exists(con):
            ensure_attempts(con)
        return _count_failure(con, event_id, now_ms=now_ms, error=exc,
                              due_ms=due, max_failures=max_failures,
                              max_wait_ms=max_wait_ms)

    return _write_label(con, event_id, due_ms=due, candle=candle)


def settle(*, db_path, now_ms, candle_fn, dry=True, **limits):
    con = _connect(db_path)
    try:
        rows = sealed_rows(con)
        if not rows:
            return {"status": "no_triples", "dry": bool(dry), "outcomes": []}
        outcomes = [settle_one(con, r, now_ms=now_ms, candle_fn=candle_fn,
                               dry=dry, **limits) for r in rows]
        overall = "error" if any(o["status"] == "error" for o in outcomes) else "checked"
        return {"status": overall, "dry": bool(dry), "outcomes": outcomes}
    finally:
        con.close()


# ------------------------------------------------------- exchange candle only

def exchange_candle(*, due_ms, now_ms, opener=None, symbol="BTCUSDT"):
    """A CLOSED 1m candle taken from the exchange payload, not reshaped.

    Returns open_ms, close_ms, close, symbol, source and closed taken from the
    response itself. If the payload cannot support a closed candle at/after due,
    this raises — it never invents timestamps to satisfy the validator.
    """
    import v21_paper_prep as P

    start = (int(due_ms) // 60_000) * 60_000
    rows = P.fetch_klines_1m(start, limit=5, opener=opener)
    for open_ms, close_ms, price in sorted(rows):
        if int(close_ms) < int(due_ms):
            continue
        # `closed` is a fact about the exchange minute, not about our clock:
        # a minute is closed when its published close_time is in the past.
        closed = int(close_ms) < int(now_ms)
        return {
            "symbol": symbol,
            "source": "binance_public_1m_kline",
            "open_ms": int(open_ms),
            "close_ms": int(close_ms),
            "close": float(price),
            "closed": bool(closed),
            "_raw_fields": "open_time,close_time,close from exchange payload",
        }
    raise P.PrepError("label_candle_unavailable")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(T2.DEFAULT_DB))
    ap.add_argument("--settle", action="store_true",
                    help="write outcomes; without it nothing is written")
    ap.add_argument("--now-ms", type=int, default=None)
    args = ap.parse_args(argv)
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
    try:
        out = settle(db_path=args.db, now_ms=now_ms,
                     candle_fn=lambda *, due_ms: exchange_candle(
                         due_ms=due_ms, now_ms=now_ms),
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

#!/usr/bin/env python3
"""NEWS Tier-2 — live trigger and label worker, DISABLED by default.

Stage-22 P0-4 gap: the collector logged `qualifying_seen` and nothing consumed it,
so a fresh event could never start a trial, and a sealed triple could never be
labelled. This module is that missing piece. It is inert unless BOTH files exist:

    ~/.local/share/news-phase4-v25/TIER2_ACTIVE        (gate flag)
    ~/.local/share/news-phase4-v25/TRIGGER_ENABLED     (this switch)

Neither exists today. `enabled()` is the only way in, and it has no write path.

Design constraints, each one a test:

  * claim BEFORE work: the trial id is claimed atomically in SQLite, so two
    collectors cannot both spend three calls on the same event;
  * one trial, three calls, no retry after an ambiguous failure - a failure
    writes a tombstone with the reason and leaves the denominator intact;
  * the event is frozen by value (payload + market snapshot + cutoff) before the
    first call, so the arms and the record cannot disagree later;
  * the quote is fetched AFTER the last arm and validated against the wall clock;
  * the label is written once, from a CLOSED 1m candle at due, and refuses to
    fabricate a price when the market data is missing - `censored`, not a guess.
"""
import json
import time
from pathlib import Path

import tier2 as T2

DATA = Path.home() / ".local/share/news-phase4-v25"
TRIGGER_FLAG = DATA / "TRIGGER_ENABLED"
CLAIMS = DATA / "tier2_claims.sqlite"


class NotEnabled(RuntimeError):
    pass


def enabled(flag=TRIGGER_FLAG, gate=T2.GATE_FILE):
    """Both switches must be present. This function never creates either."""
    return Path(flag).exists() and Path(gate).exists()


def require_enabled(flag=TRIGGER_FLAG):
    if not enabled(flag):
        raise NotEnabled("tier2_trigger_disabled")
    return T2.require_gate()


# ------------------------------------------------------------------ claiming

CLAIM_SCHEMA = """
CREATE TABLE IF NOT EXISTS tier2_claim(
 id INTEGER PRIMARY KEY CHECK(id = 1),
 trial_id TEXT NOT NULL, event_id TEXT NOT NULL, claimed_ms INTEGER NOT NULL,
 state TEXT NOT NULL);
"""


def claim_trial(con, *, trial_id, event_id, now_ms):
    """Claim the ONE trial atomically. Returns the claim or raises.

    BEGIN IMMEDIATE before the read means two collectors cannot both see an
    empty table; the CHECK(id=1) means the table can never hold two trials.
    """
    con.executescript(CLAIM_SCHEMA)
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT trial_id, event_id FROM tier2_claim").fetchone()
        if row is not None:
            con.rollback()
            raise T2.Tier2Error("trial_already_claimed:" + str(row[0]))
        con.execute("INSERT INTO tier2_claim(id,trial_id,event_id,claimed_ms,state)"
                    " VALUES(1,?,?,?,'claimed')", (trial_id, event_id, int(now_ms)))
        con.commit()
    except T2.Tier2Error:
        raise
    except Exception:
        con.rollback()
        raise
    return {"trial_id": trial_id, "event_id": event_id, "state": "claimed"}


def claim_state(con):
    con.executescript(CLAIM_SCHEMA)
    row = con.execute("SELECT trial_id, event_id, state FROM tier2_claim").fetchone()
    return None if row is None else {"trial_id": row[0], "event_id": row[1],
                                     "state": row[2]}


def close_claim(con, *, state, now_ms):
    """Close the claim atomically. Terminal states are one-way.

    The read and the write happen under BEGIN IMMEDIATE with the current state in
    the WHERE clause, so two closers cannot both think they won: the loser sees
    rowcount 0 and is told the claim was already terminal.
    """
    if state not in ("done", "aborted", "unknown"):
        raise T2.Tier2Error("bad_claim_state:" + str(state))
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute("SELECT state FROM tier2_claim").fetchone()
        if cur is None:
            con.rollback()
            raise T2.Tier2Error("no_claim")
        if cur[0] in ("done", "aborted", "unknown"):
            con.rollback()
            raise T2.Tier2Error("claim_already_terminal:" + cur[0])
        out = con.execute(
            "UPDATE tier2_claim SET state=? WHERE id=1 AND state=?",
            (state, cur[0]))
        if out.rowcount != 1:
            con.rollback()
            raise T2.Tier2Error("claim_close_raced")
        con.commit()
    except T2.Tier2Error:
        raise
    except Exception:
        con.rollback()
        raise
    return state


# ------------------------------------------------------------------- trigger

def on_new_event(*, con, event, prompts, executor, quote_fn, now_ms, trial_id,
                 attestation_path, gate_check=None, clock=None,
                 published_max_ms=None):
    """Consume ONE eligible event. Refuses everything it cannot vouch for.

    The event must already be inside its freshness budget - the collector's
    filter decides *eligibility*, this function decides *admissibility*, and it
    re-checks rather than trusting the caller.
    """
    act = (gate_check or require_enabled)()
    if act["trial_id"] != trial_id:
        raise T2.Tier2Error("trial_id_mismatch")
    claim = claim_trial(con, trial_id=trial_id, event_id=event["event_id"],
                        now_ms=now_ms)
    try:
        import tier2_orchestrator as O
        out = O.run_trial(con=con, event=event, prompts=prompts,
                          executor=executor, quote_fn=quote_fn, now_ms=now_ms,
                          trial_id=trial_id, attestation_path=attestation_path,
                          gate_check=gate_check, clock=clock,
                          published_max_ms=published_max_ms)
    except Exception as exc:
        close_claim(con, state="aborted", now_ms=now_ms)
        raise T2.Tier2Error("trial_aborted:" + type(exc).__name__ + ":"
                            + str(exc)) from None
    close_claim(con, state="done", now_ms=now_ms)
    return dict(out, claim=claim)


# -------------------------------------------------------------- label worker

LABEL_SYMBOL = "BTCUSDT"
LABEL_MAX_TRANSIENT_FAILURES = 3
LABEL_MAX_WAIT_MS = 6 * 3_600_000      # past due, how long we keep retrying
LABEL_ATTEMPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tier2_label_attempts(
 event_id TEXT PRIMARY KEY,
 attempts INTEGER NOT NULL,
 first_failure_ms INTEGER, last_failure_ms INTEGER,
 last_error TEXT, state TEXT NOT NULL);
"""


def _sealed_facts(con, event_id):
    """The due moment and symbol come from the SEALED ROW, never from a caller.

    A caller-supplied `due_ms` is how a wrong-but-plausible label enters the
    record: a label written for an earlier due looks legitimate and cannot be
    told apart afterwards. So the value is read here and any mismatch refuses.
    """
    row = con.execute(
        "SELECT entry_observed_ms, horizon_ms FROM tier2_triples WHERE event_id=?",
        (event_id,)).fetchone()
    if row is None:
        raise T2.Tier2Error("label_unknown_triple:" + str(event_id))
    entry_ms, horizon_ms = int(row[0]), int(row[1])
    if entry_ms <= 0 or horizon_ms <= 0:
        raise T2.Tier2Error("sealed_entry_or_horizon_invalid")
    return {"due_ms": entry_ms + horizon_ms, "entry_observed_ms": entry_ms,
            "horizon_ms": horizon_ms, "symbol": LABEL_SYMBOL}


def _attempts(con):
    con.executescript(LABEL_ATTEMPTS_SCHEMA)
    return con


def label_once(*, con, event_id, due_ms, candle_fn, now_ms, symbol=None,
               max_failures=LABEL_MAX_TRANSIENT_FAILURES,
               max_wait_ms=LABEL_MAX_WAIT_MS):
    """Write the +4 h outcome exactly once, or refuse. Never fabricates a price.

    The due moment is read from the sealed triple and compared with the caller's
    value: a mismatch is refused rather than corrected, because silently picking
    one of two disagreeing deadlines is how a wrong label survives review.

    A candle failure is PENDING with a bounded attempt counter, not a terminal
    censor: a transient outage must not permanently censor an observable row.
    Censor only after `max_failures` attempts or `max_wait_ms` past due.
    """
    _attempts(con)
    facts = _sealed_facts(con, event_id)
    if int(due_ms) != facts["due_ms"]:
        raise T2.Tier2Error(f"label_due_mismatch:caller={int(due_ms)}"
                            f":sealed={facts['due_ms']}")
    want_symbol = symbol or facts["symbol"]
    if want_symbol != facts["symbol"]:
        raise T2.Tier2Error("label_symbol_mismatch:" + str(want_symbol))

    con.execute("BEGIN IMMEDIATE")
    try:
        existing = con.execute("SELECT label_state FROM tier2_outcomes WHERE "
                               "event_id=?", (event_id,)).fetchone()
        if existing is not None:
            con.rollback()
            return {"label_state": existing[0], "event_id": event_id,
                    "already_labelled": True}
        due = facts["due_ms"]
        if int(now_ms) < due:
            con.rollback()
            return {"label_state": "pending", "event_id": event_id,
                    "due_ms": due, "reason": "not_due_yet"}

        row = con.execute("SELECT attempts, state FROM tier2_label_attempts "
                          "WHERE event_id=?", (event_id,)).fetchone()
        attempts, state = (0, "pending") if row is None else (row[0], row[1])
        if state == "censored":
            con.rollback()
            return {"label_state": "censored", "event_id": event_id,
                    "already_labelled": True}
        if state == "exhausted":
            con.rollback()
            raise T2.Tier2Error("label_wait_exhausted:" + str(event_id))

        try:
            candle = candle_fn(due_ms=due)
            T2.validate_candle(candle, due, symbol=facts["symbol"])
        except Exception as exc:
            # Validation runs BEFORE any write, so an invalid candle cannot
            # leave a half-written outcome behind.
            attempts += 1
            waited = int(now_ms) - due
            exhausted = attempts >= max_failures or waited > max_wait_ms
            if exhausted:
                con.execute(
                    "INSERT OR REPLACE INTO tier2_outcomes(event_id,due_ms,price,"
                    "observed_ms,source,drift_ms,label_state) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (event_id, due, 0.0, int(now_ms), "unavailable", -1,
                     "censored"))
                con.execute(
                    "INSERT OR REPLACE INTO tier2_label_attempts VALUES(?,?,?,?,?,?)",
                    (event_id, attempts, int(now_ms), int(now_ms),
                     type(exc).__name__, "censored"))
                con.commit()
                return {"label_state": "censored", "event_id": event_id,
                        "attempts": attempts, "reason": type(exc).__name__}
            con.execute(
                "INSERT OR REPLACE INTO tier2_label_attempts "
                "(event_id,attempts,first_failure_ms,last_failure_ms,last_error,"
                "state) VALUES(?,?,COALESCE((SELECT first_failure_ms FROM "
                "tier2_label_attempts WHERE event_id=?),?),?,?,?)",
                (event_id, attempts, event_id, int(now_ms), int(now_ms),
                 type(exc).__name__, "pending"))
            con.commit()
            return {"label_state": "pending", "event_id": event_id,
                    "attempts": attempts, "reason": type(exc).__name__}

        drift = int(candle["close_ms"]) - due
        con.execute(
            "INSERT INTO tier2_outcomes(event_id,due_ms,price,observed_ms,"
            "source,drift_ms,label_state) VALUES(?,?,?,?,?,?,?)",
            (event_id, due, float(candle["close"]), int(candle["close_ms"]),
             "binance_public_1m_close", drift, "labelled"))
        con.execute("DELETE FROM tier2_label_attempts WHERE event_id=?",
                    (event_id,))
        con.commit()
        return {"label_state": "labelled", "event_id": event_id,
                "price": candle["close"], "drift_ms": drift}
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise

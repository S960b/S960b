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
    """Terminal claim states are one-way: aborted or done, never re-opened."""
    if state not in ("done", "aborted", "unknown"):
        raise T2.Tier2Error("bad_claim_state:" + str(state))
    cur = con.execute("SELECT state FROM tier2_claim").fetchone()
    if cur is None:
        raise T2.Tier2Error("no_claim")
    if cur[0] in ("done", "aborted", "unknown"):
        raise T2.Tier2Error("claim_already_terminal:" + cur[0])
    con.execute("UPDATE tier2_claim SET state=? WHERE id=1", (state,))
    con.commit()
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

def label_once(*, con, event_id, due_ms, candle_fn, now_ms):
    """Write the +4 h outcome exactly once, or refuse. Never fabricates a price.

    Retry-safe: an existing outcome row means the work is done, so a scheduler
    that runs late (or twice) cannot double-write or overwrite a label.
    """
    existing = con.execute("SELECT label_state FROM tier2_outcomes WHERE "
                           "event_id=?", (event_id,)).fetchone()
    if existing is not None:
        return {"label_state": existing[0], "event_id": event_id,
                "already_labelled": True}

    sealed = con.execute("SELECT 1 FROM tier2_triples WHERE event_id=?",
                         (event_id,)).fetchone()
    if sealed is None:
        raise T2.Tier2Error("label_unknown_triple:" + str(event_id))

    if int(now_ms) < int(due_ms):
        return {"label_state": "pending", "event_id": event_id,
                "due_ms": due_ms, "reason": "not_due_yet"}

    try:
        candle = candle_fn(due_ms=due_ms)
    except Exception as exc:
        # A market-data outage is a censored row, not a made-up price.
        con.execute("INSERT INTO tier2_outcomes(event_id,due_ms,price,"
                    "observed_ms,source,drift_ms,label_state) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (event_id, int(due_ms), 0.0, int(now_ms),
                     "unavailable", -1, "censored"))
        con.commit()
        return {"label_state": "censored", "event_id": event_id,
                "reason": type(exc).__name__}

    T2.validate_candle(candle, due_ms, symbol="BTCUSDT")
    drift = int(candle["close_ms"]) - int(due_ms)
    con.execute("INSERT INTO tier2_outcomes(event_id,due_ms,price,observed_ms,"
                "source,drift_ms,label_state) VALUES(?,?,?,?,?,?,?)",
                (event_id, int(due_ms), float(candle["close"]),
                 int(candle["close_ms"]), "binance_public_1m_close", drift,
                 "labelled"))
    con.commit()
    return {"label_state": "labelled", "event_id": event_id,
            "price": candle["close"], "drift_ms": drift}

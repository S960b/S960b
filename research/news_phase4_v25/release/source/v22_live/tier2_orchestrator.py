#!/usr/bin/env python3
"""NEWS Tier-2 — one-trial orchestrator (OFFLINE HARNESS).

Stage-21 G: the previous code had no path from an event to a sealed triple, so a
fresh event could not have fired anything. This module is that path, and it is
written to be run against a disposable database with injected call results until
every P0 item is closed. It never reaches the network on its own.

Sequence, each step refusing rather than continuing:

    1. gate      require_gate(): flag + owner-approved activation record
    2. event     freshness and time discipline at DECISION time
    3. reserve   all three attempts, atomically, before any call
    4. calls     three invocations, budget consumed before each one
    5. quote     sampled strictly AFTER the last arm, validated
    6. seal      one immutable observational_tier2 row
    7. label     scheduled at entry + 4 h from a CLOSED 1m candle

Any failure stops the run and leaves the attempt rows as tombstones. A second
run for the same trial is refused, so a crash cannot be retried into a second
triple.
"""
import time
from pathlib import Path

import v22_arms as A
import tier2 as T2

TRIAL_STEPS = ("gate", "event", "reserve", "calls", "quote", "seal", "label")


class OrchestratorError(RuntimeError):
    pass


def run_trial(*, con, event, prompts, executor, quote_fn, now_ms,
              trial_id, attestation_path, gate_check=None, candle_fn=None,
              published_max_ms=None, clock=None, frozen_sha256=None,
              cohort_extra="|tier2", protocol_file_sha256=None,
              symbol=T2.QUOTE_SYMBOL, protocol_id=T2.PROTOCOL_ID,
              prompts_version=T2.PROMPTS_VERSION):
    """Execute one Tier-2 trial end to end. Returns a step-by-step record.

    `executor`, `quote_fn` and `candle_fn` are injected so the harness can run
    entirely offline while the production wiring substitutes the real ones.
    """
    steps = []

    def step(name, **info):
        steps.append({"step": name, "at_ms": int(time.time() * 1000), **info})

    # 1. gate -------------------------------------------------------------
    act = (gate_check or T2.require_gate)()
    if act["trial_id"] != trial_id:
        raise OrchestratorError("trial_id_mismatch")
    step("gate", trial_id=trial_id)

    # 2. event ------------------------------------------------------------
    # This is the pre-inference decision clock. Model latency must never
    # redefine eligibility; the post-response clock is for entry/quote only.
    decision_ms = int(now_ms)
    T2.validate_event(event, decision_ms, published_max_ms=published_max_ms)
    step("event", event_id=event["event_id"], cohort=event["cohort"],
         decision_ms=decision_ms,
         age_s=(decision_ms - int(event.get("observed_ms")
                                  or event["published_ms"])) // 1000)

    # 3. reserve ----------------------------------------------------------
    T2.open_attempts(con)
    T2.reserve_attempts(con, trial_id=trial_id, now_ms=now_ms)
    step("reserve", arms=list(A.ARM_NAMES))

    # 4. calls ------------------------------------------------------------
    try:
        arms = executor(prompts, now_ms=now_ms, con=con, trial_id=trial_id)
    except Exception as exc:
        for arm in A.ARM_NAMES:
            row = con.execute("SELECT state FROM tier2_attempts "
                              "WHERE trial_id=? AND arm=?", (trial_id, arm)).fetchone()
            if row and row[0] in ("prepared", "started"):
                T2.mark_attempt(con, trial_id=trial_id, arm=arm, state="failed",
                                now_ms=int(time.time() * 1000),
                                detail="orchestrator:" + type(exc).__name__)
        raise
    step("calls", arms=[a["arm"] for a in arms],
         used=T2.calls_used(con, trial_id))

    # 5. quote ------------------------------------------------------------
    last_arm_ms = max(int(a["completed_ms"]) for a in arms)
    quote = quote_fn(after_ms=last_arm_ms)
    # The quote is taken AFTER the arms by construction, so the decision clock
    # used for the "not in the future" check has to be taken after the quote,
    # not before it. Using the pre-call clock would reject every valid quote.
    # The clock used to reject a "future" quote must be the REAL wall clock, not
    # a value derived from the quote itself - otherwise any future timestamp
    # validates against itself. In the offline harness the clock is injected.
    wall_ms = int(clock() if clock is not None else time.time() * 1000)
    if wall_ms < int(now_ms):
        raise OrchestratorError("clock_went_backwards")
    T2.validate_quote(quote, last_arm_ms, event["published_ms"],
                      now_ms=wall_ms, symbol=symbol)
    now_ms = max(int(now_ms), wall_ms)
    step("quote", price=quote["price"], observed_ms=quote["observed_ms"],
         source=quote["source"])

    # 6. seal -------------------------------------------------------------
    prompts_sha = T2.canonical_sha({a: prompts[a] for a in A.ARM_NAMES})
    sealed = T2.seal_triple(con=con, event=event, arm_results=arms, quote=quote,
                            attestation_path=attestation_path,
                            now_ms=max(int(now_ms), quote["observed_ms"] + 1),
                            prompts_sha256=prompts_sha,
                            published_max_ms=published_max_ms,
                            decision_ms=decision_ms, trial_id=trial_id,
                            frozen_sha256=frozen_sha256,
                            cohort_extra=cohort_extra,
                            protocol_file_sha256=protocol_file_sha256,
                            symbol=symbol, protocol_id=protocol_id,
                            prompts_version=prompts_version)
    step("seal", status=sealed["status"], seal=sealed.get("seal"),
         cohort=sealed.get("cohort"))

    # 7. label schedule ---------------------------------------------------
    due_ms = int(quote["observed_ms"]) + T2.HORIZON_MS
    label_plan = {"due_ms": due_ms, "scheduled": True,
                  "horizon_ms": T2.HORIZON_MS}
    if candle_fn is not None:
        # Offline harness passes a candle lookup; production leaves it to a
        # scheduled check and never fabricates a price.
        label_plan["label_state"] = "pending"
    step("label", **label_plan)
    return {"status": "sealed", "steps": steps, "seal": sealed.get("seal"),
            "due_ms": due_ms, "cohort": sealed.get("cohort")}


def write_label(*, con, event_id, due_ms, candle, now_ms,
                symbol=T2.QUOTE_SYMBOL):
    """Write the +4 h outcome from a CLOSED candle, or refuse.

    The D15 rule is reused: the exit is the first closed 1m candle at/after due,
    drift in [0, 60 s). A missing or invalid candle leaves the row pending - a
    fabricated price would be worse than a censored row.
    """
    T2.validate_candle(candle, due_ms, symbol=symbol)
    drift = int(candle["close_ms"]) - int(due_ms)
    cur = con.execute(
        "INSERT INTO tier2_outcomes(event_id,due_ms,price,observed_ms,source,"
        "drift_ms,label_state) VALUES(?,?,?,?,?,?,?)",
        (event_id, int(due_ms), float(candle["close"]),
         int(candle["close_ms"]), "binance_public_1m_close", drift, "labelled"))
    con.commit()
    return {"label_state": "labelled", "event_id": event_id, "price": candle["close"],
            "drift_ms": drift}

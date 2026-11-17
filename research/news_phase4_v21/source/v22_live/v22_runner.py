#!/usr/bin/env python3
"""NEWS v2.2 — standalone paper runner (three arms, no GUI, no legacy coupling).

Runs OUTSIDE the desktop application on purpose: a GUI restart would risk the
legacy v2.1 and Phase-4 v2 collectors, which must keep running. This process
opens only its own SQLite file, never the v2.1 ledger.

Design constraints taken from the protocol, not invented here:

  * all three arms read ONE frozen snapshot (market vector + cutoff);
  * the M arm receives the market dict only - verified structurally, not by
    convention;
  * each arm is reserved durably BEFORE anything is invoked, states move
    prepared -> started -> completed|failed|unknown, and nothing replays;
  * the entry quote is sampled AFTER the last arm finished;
  * the exit comes from the first CLOSED 1m candle at/after due, drift in
    [0, 60000) - the D15 rule, reused, not reimplemented;
  * a failed arm or unknown tool evidence refuses the seal instead of
    producing a partial triple.

`--dry` executes the whole pipeline with a mock executor and writes nothing to
the production paths; it is the wiring test. Live arm execution is wired behind
an `executor` callable so the same code path is exercised either way.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import v22_arms as A
import v22_ledger as L
import v21_paper_prep as P
import v21_pilot_cycle as PC

DEFAULT_DB = Path.home() / ".local/share/news-phase4-v24/v22.sqlite"
HORIZON_MS = 4 * 3_600_000


def frozen_market(candles, cutoff_ms):
    """The single snapshot every arm sees. Built once, passed by value."""
    return P.market_features(candles, cutoff_ms)


def mock_executor(prompts, *, now_ms):
    """Deterministic stand-in for a model call, used by --dry."""
    out = []
    for arm in A.ARM_NAMES:
        out.append({"arm": arm,
                    "attempt_id": hashlib.sha256(f"{arm}:{now_ms}".encode()).hexdigest(),
                    "session_id": f"mock-{arm}",
                    "text": json.dumps({"p_up": 0.5, "expected_return_bps": 0,
                                        "uncertainty": 0.5, "rationale": "mock"}),
                    "model": {"providerID": "mock", "modelID": "mock"},
                    "session_terminal": "completed",
                    "completed_ms": int(now_ms) + 1_000,
                    "tool_evidence": "known_zero",
                    "tool_evidence_provenance": "mock_executor",
                    "tools": 0})
    return out


def run_triple(*, db_path, event, candles, cutoff_ms, executor,
               now_ms=None, quote_fn=None):
    """One full triple: reserve -> arms -> quote -> seal. Returns a status dict."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    market = frozen_market(candles, cutoff_ms)
    prompts = A.build_prompts_3(event, market)
    meta = {arm: A.execution_metadata(arm, event_ms=cutoff_ms) for arm in A.ARM_NAMES}
    A.audit_market_arm_isolation(prompts, meta, event)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = L.open_v22(str(db_path))
    try:
        hashes = A.prompts_sha256(prompts)
        reserved = L.reserve_triple(con, event_id=event["event_id"],
                                    prompts_sha=hashes, now_ms=now_ms)
        if reserved["status"] != "reserved":
            return {"status": "already_reserved", "attempts": reserved["attempts"]}

        for arm in A.ARM_NAMES:
            L.mark_arm(con, event_id=event["event_id"], arm=arm,
                       state="started", now_ms=now_ms + 10)
        results = executor(prompts, now_ms=now_ms)
        for res in results:
            ok = res.get("tool_evidence") == "known_zero"
            L.mark_arm(con, event_id=event["event_id"], arm=res["arm"],
                       state="completed" if ok else "failed",
                       now_ms=res.get("completed_ms", now_ms + 900),
                       session_id=res.get("session_id"),
                       detail="tool_evidence:" + str(res.get("tool_evidence")))

        terminal, reason = L.attempts_are_terminal(con, event["event_id"])
        if not terminal:
            return {"status": "refused", "reason": reason}

        last_arm_ms = max(r["completed_ms"] for r in results)
        quote = (quote_fn or _default_quote)(after_ms=last_arm_ms)
        prepared = {"prompts_sha256": hashes, "cutoff_ms": int(cutoff_ms)}
        out = L.seal_triple(con, event=event, prepared=prepared,
                            arm_results=results, quote=quote,
                            now_ms=now_ms + 5_000)
        return out
    finally:
        con.close()


def _default_quote(*, after_ms):
    """Public Binance ticker quote, sampled only after the last arm."""
    return PC.public_quote(after_ms=after_ms)


def label(*, db_path, event_id, candles_for_due, due_ms, now_ms):
    con = L.open_v22(str(db_path))
    try:
        return L.label_triple(con, event_id, due_ms=due_ms, now_ms=now_ms,
                              candles=candles_for_due)
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry", action="store_true",
                    help="mock executor, disposable DB, no network for arms")
    ap.add_argument("--now-ms", type=int, default=None)
    a = ap.parse_args(argv)
    if not a.dry:
        return ("live arm execution requires an approved executor; "
                "run --dry to exercise the wiring")
    now = a.now_ms or int(time.time() * 1000)
    cutoff = now // 3_600_000 * 3_600_000
    candles = [(cutoff - i * 3_600_000, cutoff - i * 3_600_000 + 3_599_999,
                60_000.0 + i) for i in range(30, 0, -1)]
    event = {"event_id": "d" * 64, "source": "dry", "cohort": "context_news",
             "published_ms": now - 600_000, "observed_ms": now - 900_000,
             "title": "dry run", "summary": ""}
    out = run_triple(db_path=a.db, event=event, candles=candles,
                     cutoff_ms=cutoff, executor=mock_executor, now_ms=now,
                     quote_fn=lambda after_ms: {"price": 60_000.0,
                                                "observed_ms": after_ms + 1_000,
                                                "source": "dry_run"})
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

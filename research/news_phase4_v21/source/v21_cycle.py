#!/usr/bin/env python3
"""NEWS v2.1 — one plugin cycle, driven by the native MultiTool session.

PAPER ONLY. This file never places orders and never touches a private Binance
endpoint. It is the only writer of the v2.1 audit journal; the plugin calls it,
it never calls the plugin.

Commands (each prints ONE JSON object on stdout):
  status      read-only snapshot of quotas, queue and records
  prepare     select an eligible event, seal the pair, reserve under the shared cap
  session     store the SDK session id for one attempt
  prompt      record that the prompt was sent for one attempt
  finish      persist the immutable record from the two arm texts
  quote       public ticker helpers (no key, no signature)
  outcomes    mature 4h labels from closed candles
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import v21_dispatch as D
import v21_paper_prep as P

ROOT = Path("/home/kali/research/news-phase4-v21")
DATA = Path("/home/kali/.local/share/news-phase4-v21")
EVENTS_DB = DATA / "v21.sqlite"
DISPATCH_DB = DATA / "dispatch.sqlite"
RECORDS_DB = DATA / "paper.sqlite"


def emit(obj):
    print(json.dumps(obj, sort_keys=True, ensure_ascii=True))
    return 0


def _events(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


PAUSE_FLAG = "PAUSED_MODEL_INFERENCE"


def inference_paused(paths):
    """A file next to the records DB stops all model work immediately.

    Config changes need a runtime reload; a file on disk does not. Any cycle
    that would spend a model call checks this first and refuses.
    """
    return (Path(paths["records"]).parent / PAUSE_FLAG).exists()


def _paths(args):
    return {"events": Path(args.events_db), "dispatch": Path(args.dispatch_db),
            "records": Path(args.records_db)}


def cmd_status(args):
    paths = _paths(args)
    out = {"policy": D.POLICY, "events_db": paths["events"].is_file(),
           "dispatch_db": paths["dispatch"].is_file(),
           "records_db": paths["records"].is_file()}
    if paths["dispatch"].is_file():
        con = D.open_db(paths["dispatch"], create=True)
        try:
            out.update(D.status(con))
        finally:
            con.close()
    if paths["records"].is_file():
        con = P.open_records(paths["records"])
        try:
            out["records"] = con.execute(
                "SELECT COUNT(*) FROM paper_records").fetchone()[0]
            out["real_records"] = con.execute(
                "SELECT COUNT(*) FROM paper_records WHERE synthetic=0"
            ).fetchone()[0]
            out["outcomes"] = con.execute(
                "SELECT COUNT(*) FROM paper_outcomes").fetchone()[0]
        finally:
            con.close()
    return emit(out)


def _preflight(paths, cutoff_hour, decision_time_ms, market=None):
    """All checks that must pass BEFORE any quota is reserved."""
    if market is None:
        market = P.fetch_market("production", cutoff_hour)
    if market.get("candle_count") != 26:
        raise P.PrepError("market_features_incomplete")
    if market["candle_closes_ms"] >= decision_time_ms:
        raise P.PrepError("candle_not_closed_before_cutoff")
    if not Path(paths["events"]).is_file():
        raise P.PrepError("events_db_missing")
    return market


def _trace(paths, payload):
    """Every prepare outcome is recorded: a silent status once cost a pair."""
    try:
        import time as _t
        entry = dict(payload)
        entry["at_ms"] = int(_t.time() * 1000)
        for key in ("prompts", "market", "arms"):
            entry.pop(key, None)
        with (Path(paths["records"]).parent / "prepare-trace.jsonl").open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


def cmd_prepare(args):
    paths = _paths(args)
    if inference_paused(paths):
        return emit({"status": "blocked", "reason": "model_inference_paused",
                     "model_called": False})
    # Two clocks: the real instant of the decision (news freshness) and the
    # last closed hour (market features). Never the same value.
    decision_time_ms = args.now_ms or time.time_ns() // 1_000_000
    cutoff_hour = P.decision_cutoff(decision_time_ms)
    try:
        if args.fixture_market:
            raw = json.loads(Path(args.fixture_market).read_text())
            candles = [(int(r[0]), int(r[6]), float(r[4])) for r in raw]
            market = P.market_features(candles, cutoff_hour)
        else:
            market = None
        market = _preflight(paths, cutoff_hour, decision_time_ms, market)
    except P.PrepError as exc:
        payload = {"status": "blocked", "reason": str(exc),
                   "decision_time_ms": decision_time_ms,
                   "market_cutoff_ms": cutoff_hour, "model_called": False}
        _trace(paths, payload)
        return emit(payload)
    dcon = D.open_db(paths["dispatch"], create=True)
    ecm = _events(paths["events"])
    try:
        import v21_selector as S
        # Ask for a batch, not one: the top story may already be reserved, and
        # with limit=1 the lane would block on it until the UTC reset.
        candidates, _decisions = S.select(ecm, decision_time_ms,
                                          limit=max(args.limit, 10))
        if not candidates:
            payload = {"status": "no_eligible_event", "reason": "no_candidates",
                       "decision_time_ms": decision_time_ms,
                       "market_cutoff_ms": cutoff_hour, "model_called": False}
            _trace(paths, payload)
            return emit(payload)
        # An already-reserved event must never be picked again: a stale
        # reservation would otherwise block the lane until the day resets,
        # because the selector keeps offering the same top story.
        taken = {r[0] for r in dcon.execute(
            "SELECT DISTINCT event_id FROM dispatch_reservations")}
        free = [c for c in candidates if c["event_id"] not in taken]
        if not free:
            payload = {"status": "no_eligible_event",
                       "reason": "all_candidates_already_reserved",
                       "candidates": [c["event_id"][:12] for c in candidates],
                       "taken": sorted(t[:12] for t in taken),
                       "decision_time_ms": decision_time_ms,
                       "market_cutoff_ms": cutoff_hour, "model_called": False}
            _trace(paths, payload)
            return emit(payload)
        ev = free[0]
        result = P.prepare(None, dcon, ev, enabled=True,
                           now_ms=decision_time_ms,
                           market_cutoff_ms=cutoff_hour, market=market,
                           synthetic=False, preflight=True)
        if result.get("status") != "ready":
            return emit({"status": "blocked", "reason": result.get("reason"),
                         "event_id": ev["event_id"],
                         "decision_time_ms": decision_time_ms,
                         "model_called": False})
        # Contract consumed by v21_paper_runner.mjs buildArmPlan(): the prompts
        # mapping is mandatory, plus the flat arms array for the caller.
        plan = [{"arm": arm, "attempt_id": aid,
                 "prompt": result["prompts"][arm]}
                for arm, aid in zip(("news", "news_market"),
                                    result["attempts"])]
        payload = {"status": "ready", "event_id": ev["event_id"],
                     "cohort": ev["cohort"],
                     "decision_time_ms": decision_time_ms,
                     "market_cutoff_ms": cutoff_hour,
                     "cutoff_ms": cutoff_hour,
                     "event_age_ms": decision_time_ms - ev["published_ms"],
                     "attempts": result["attempts"],
                     "arms": plan,
                     "prompts": result["prompts"],
                     "prompts_sha256": result["prompts_sha256"],
                     "market": market,
                     "calls_today": result["calls_today"],
                     "pairs_today": result["pairs_today"],
                     "model_called": False}
        _trace(paths, payload)
        return emit(payload)
    except Exception as exc:
        code = str(exc) if isinstance(exc, P.PrepError) else type(exc).__name__
        payload = {"status": "blocked", "reason": code,
                   "decision_time_ms": decision_time_ms, "model_called": False}
        _trace(paths, payload)
        return emit(payload)
    finally:
        ecm.close()
        dcon.close()


def cmd_session(args):
    con = D.open_db(Path(args.dispatch_db), create=True)
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "UPDATE dispatch_reservations SET state=state WHERE attempt_id=?",
            (args.attempt_id,))
        if cur.rowcount != 1:
            con.rollback()
            return emit({"status": "unknown_attempt"})
        con.execute("INSERT INTO dispatch_log(attempt_id,event_id,arm,action,"
                    "at_ms,synthetic) SELECT attempt_id,event_id,arm,"
                    "'session_created',?,synthetic FROM dispatch_reservations "
                    "WHERE attempt_id=?", (int(time.time() * 1000),
                                           args.attempt_id))
        con.commit()
    finally:
        con.close()
    return emit({"status": "session_recorded", "attempt_id": args.attempt_id})


def cmd_prompt(args):
    con = D.open_db(Path(args.dispatch_db), create=True)
    try:
        con.execute("INSERT INTO dispatch_log(attempt_id,event_id,arm,action,"
                    "at_ms,synthetic) SELECT attempt_id,event_id,arm,"
                    "'prompt_sent',?,synthetic FROM dispatch_reservations "
                    "WHERE attempt_id=?", (int(time.time() * 1000),
                                           args.attempt_id))
        con.commit()
    finally:
        con.close()
    return emit({"status": "prompt_marked", "attempt_id": args.attempt_id})


MAX_COMPLETIONS_PER_PAIR = 8      # a runaway tool loop is not a forecast
MAX_INPUT_TOKENS_PER_PAIR = 400_000
MAX_COST_USD_PER_PAIR = 0.20


def _pair_cost(records_db, event_id):
    """Completions, input tokens and cost for one event's two sessions."""
    gw = Path.home() / ".local/share/gigatool/opencode.db"
    if not gw.is_file():
        return None
    con = sqlite3.connect(f"file:{gw}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            "SELECT m.data FROM message m JOIN session s ON s.id=m.session_id "
            "WHERE s.title LIKE ?", (f"%{event_id[:12]}%",)).fetchall()
    finally:
        con.close()
    compl = tin = 0
    cost = 0.0
    for (data,) in rows:
        msg = json.loads(data) if isinstance(data, str) else data
        if msg.get("role") != "assistant":
            continue
        compl += 1
        tok = msg.get("tokens") or {}
        tin += int(tok.get("input") or 0)
        cost += float(msg.get("cost") or 0.0)
    return {"completions": compl, "input_tokens": tin, "cost_usd": round(cost, 6)}


def cmd_finish(args):
    body = json.loads(sys.stdin.read() or "{}")
    results = body.get("results")
    if not isinstance(results, list) or len(results) != 2:
        return emit({"status": "refused", "reason": "incomplete_pair"})
    paths = _paths(args)
    ecm = _events(paths["events"])
    try:
        event = P.snapshot_event(ecm, body["event_id"])
    finally:
        ecm.close()
    rcon = P.open_records(paths["records"])
    dcon = D.open_db(paths["dispatch"], create=True)
    try:
        prepared = {"cutoff_ms": int(body.get("market_cutoff_ms",
                                              body["cutoff_ms"])),
                    "reserved_ms": int(body.get("decision_time_ms",
                                                body["cutoff_ms"])),
                    "prompts_sha256": body["prompts_sha256"]}
        spend = _pair_cost(paths["records"], body["event_id"])
        usage = spend or {"completions": 0, "input_tokens": 0, "cost_usd": 0.0}
        if (usage["completions"] > MAX_COMPLETIONS_PER_PAIR
                or usage["input_tokens"] > MAX_INPUT_TOKENS_PER_PAIR
                or usage["cost_usd"] > MAX_COST_USD_PER_PAIR):
            # Fail closed: refuse to seal a run whose spend says the model went
            # off exploring, which also means its information set is suspect.
            return emit({"status": "refused",
                         "reason": "budget_circuit_breaker",
                         "usage": usage,
                         "limits": {"completions": MAX_COMPLETIONS_PER_PAIR,
                                    "input_tokens": MAX_INPUT_TOKENS_PER_PAIR,
                                    "cost_usd": MAX_COST_USD_PER_PAIR}})
        out = P.record_pair(rcon, event=event, prepared=prepared,
                            arm_results=results,
                            quote={"price": float(body["price"]),
                                   "observed_ms": int(body["observed_ms"])},
                            now_ms=int(time.time() * 1000), synthetic=False)
        if out.get("status") in ("recorded", "already_recorded"):
            dcon.execute("BEGIN IMMEDIATE")
            dcon.execute("UPDATE dispatch_reservations SET state='completed' "
                         "WHERE event_id=? AND synthetic=0",
                         (body["event_id"],))
            dcon.commit()
        out["usage"] = usage
        return emit(out)
    except Exception as exc:
        code = str(exc) if isinstance(exc, P.PrepError) else type(exc).__name__
        # Diagnostics are essential: a silent refusal wasted a real pair once.
        try:
            import time as _t
            diag = Path(paths["records"]).parent / "finish-refusals.jsonl"
            entry = {
                "at_ms": int(_t.time() * 1000),
                "event_id": str(body.get("event_id"))[:64],
                "reason": code,
                "arms": [{"arm": r.get("arm"),
                          "model": r.get("model"),
                          "len": len(str(r.get("text", ""))),
                          "tail": str(r.get("text", ""))[-300:]}
                         for r in results],
            }
            with diag.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
        except Exception:
            pass
        return emit({"status": "refused", "reason": code})
    finally:
        rcon.close()
        dcon.close()


def cmd_quote(args):
    import urllib.request
    try:
        with urllib.request.urlopen(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
                timeout=15) as resp:
            payload = json.loads(resp.read())
        price = P.parse_quote(payload)
    except Exception as exc:
        return emit({"status": "quote_unavailable",
                     "reason": type(exc).__name__})
    return emit({"status": "ok", "price": price,
                 "observed_ms": time.time_ns() // 1_000_000,
                 "source": "binance_public_ticker"})


def cmd_outcomes(args):
    """Mature 4h labels on the FIRST 1m close at or after the due instant.

    The old loop picked the hourly candle ending BEFORE the due time, which
    labelled a 3h-to-4h move as a 4h outcome. A candle is now only accepted
    when it closes at or after due, is fully closed, and drifts by at most a
    minute; anything else stays pending with the reason logged.
    """
    paths = _paths(args)
    rcon = P.open_records(paths["records"])
    settled = 0
    try:
        rows = rcon.execute(
            "SELECT r.event_id,e.price,e.observed_ms FROM paper_records r "
            "JOIN paper_entries e ON e.event_id=r.event_id "
            "WHERE r.state='complete' AND r.synthetic=0").fetchall()
        now_ms = args.now_ms or time.time_ns() // 1_000_000
        pending, failures = [], []
        for event_id, entry_price, entry_ms in rows:
            due = int(entry_ms) + 4 * 3_600_000
            if now_ms < due:
                pending.append(event_id)
                continue
            already = rcon.execute(
                "SELECT 1 FROM paper_outcomes WHERE event_id=? AND horizon_h=4",
                (event_id,)).fetchone()
            if already:
                continue
            try:
                exit_row = P.resolve_exit(due, now_ms)
            except P.PrepError as exc:
                failures.append({"event_id": event_id,
                                 "reason": str(exc),
                                 "due_ms": due})
                pending.append(event_id)
                continue
            P.record_outcome(rcon, event_id, 4, exit_row["exit_ms"],
                             exit_row["price"], now_ms,
                             drift_ms=exit_row["drift_ms"],
                             method=exit_row["method"], due_ms=due)
            settled += 1
        return emit({"status": "outcomes_done", "settled": settled,
                     "pending": len(pending), "failures": failures})
    finally:
        rcon.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("status", "prepare", "session",
                                       "prompt", "finish", "quote", "outcomes"))
    p.add_argument("--events-db", default=str(EVENTS_DB))
    p.add_argument("--dispatch-db", default=str(DISPATCH_DB))
    p.add_argument("--records-db", default=str(RECORDS_DB))
    p.add_argument("--attempt-id", default="")
    p.add_argument("--now-ms", type=int, default=None)
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--fixture-market", default=None,
                   help="offline only: JSON candles, bypasses the network")
    a = p.parse_args(argv)
    handler = {"status": cmd_status, "prepare": cmd_prepare,
               "session": cmd_session, "prompt": cmd_prompt,
               "finish": cmd_finish, "quote": cmd_quote,
               "outcomes": cmd_outcomes}[a.command]
    return handler(a)


if __name__ == "__main__":
    raise SystemExit(main())

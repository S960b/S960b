#!/usr/bin/env python3
"""NEWS v2.5 observational Tier-2, separate paper-only 100-event series.

NOT ARMED by installation. Needs owner-staged, audited activation and gate.
Default CLI is read-only status. --execute can call the existing model via the
unchanged prompt/client path; requires new independent GO before EVERY call.
No trading API is imported or used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "release" / "source"
LIVE = SOURCE / "v22_live"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
if str(LIVE) not in sys.path:
    sys.path.insert(0, str(LIVE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import series_guard as G
import tier2 as T2
import tier2_forward_driver as F
import tier2_orchestrator as O
import tier2_label_watch as LW
import v21_paper_prep as P
import v22_arms as A
import v24_collector as COL

ROOT = Path.home() / ".local/share/news-phase4-v25-series100"
DB = ROOT / "tier2_series.sqlite"
ATTEST = ROOT / "attestation.jsonl"
ACTIVATION = ROOT / "TIER2_SERIES_ACTIVATION.json"
GATE = ROOT / "SERIES_TRIGGER_ENABLED"
PROTOCOL = HERE / "TIER2_SERIES_PROTOCOL.md"
MODEL = "deepseek-v4-flash"
EXPECTED_ARMS = ("news", "news_market", "market")


class SeriesError(RuntimeError):
    pass


def ms():
    return time.time_ns() // 1_000_000


def protocol_sha():
    return hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()


def approved():
    """Never auto-stage or create the owner gate."""
    a = G.validate_activation(activation_path=ACTIVATION, gate_path=GATE)
    if a.get("protocol_sha256") != protocol_sha():
        raise SeriesError("series_protocol_sha_mismatch")
    if a.get("model") != MODEL or a.get("prompts_version") != T2.PROMPTS_VERSION:
        raise SeriesError("model_or_prompts_changed")
    return a


def store():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ROOT.is_symlink():
        raise SeriesError("series_root_is_symlink")
    # Existing validated Tier-2 seal/binding tables + independent series ledger.
    with closing(T2.open_tier2(DB)):
        pass
    return G.open_db(DB)


def choose(con, at_ms):
    claimed = con.execute(
        "SELECT event_id,semantic_cluster_id FROM series_claims").fetchall()
    excluded = {r[0] for r in claimed}
    clusters = {r[1] for r in claimed}
    # If the same publication appears from multiple feeds it remains ONE
    # semantic event. Skip each alternate occurrence without model calls.
    with closing(sqlite3.connect(F.FEED_DB.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=5)) as feed:
        for _ in range(256):
            event = F.candidate(feed, at_ms, exclude_ids=excluded)
            if event is None:
                return None
            semantic = COL.semantic_cluster_id(event["title"],
                                               int(event["published_ms"]))
            if semantic not in clusters:
                return (event, semantic)
            excluded.add(event["event_id"])
    raise SeriesError("candidate_duplicate_scan_limit")


def model_executor(*, event_id, series_id, attestation_path):
    """Use existing wire client, raw recorder, attestation, strict arm schema."""
    import tier2_raw_store as RAW
    import v22_toolfree_client as TF
    import v22_attestation as AT

    def execute(prompts, *, now_ms, con=None, trial_id=None):
        if con is None or not trial_id:
            raise SeriesError("missing_transactional_context")
        results = []
        for arm in A.ARM_NAMES:
            if arm not in EXPECTED_ARMS:
                raise SeriesError("unexpected_arm")
            act = approved()
            if act["series_id"] != series_id:
                raise SeriesError("series_changed_during_execution")
            T2.mark_attempt(con, trial_id=trial_id, arm=arm, state="started",
                            now_ms=ms())
            started = ms()
            # Independently durable GLOBAL budget before provider interaction.
            G.reserve_model_call(con, event_id=event_id, arm=arm, at_ms=started)
            # Keep existing per-event 3-call unique-index budget as second fence.
            T2.record_invocation(con, trial_id=trial_id, arm=arm, now_ms=started,
                                 session_id=None, limit=3)
            capture = RAW.raw_capture(
                attestation_path=attestation_path, trial_id=trial_id, arm=arm)
            response = TF.call(model=MODEL, system=T2.ARM_SYSTEM_T2,
                               user=prompts[arm], raw_capture=capture)
            rid = response.get("provider_request_id")
            # Return from a provider doesn't prove valid content. The slot is
            # nevertheless consumed and its real response id recorded.
            G.record_response(con, event_id=event_id, arm=arm, response_id=rid)
            T2.record_call_response(con, trial_id=trial_id, arm=arm,
                                    provider_response_id=rid)
            RAW.verify_wire(attestation_path=attestation_path, trial_id=trial_id,
                            arm=arm, request_sha256=response["request_sha256"],
                            response_sha256=response["response_sha256"])
            forwarded = AT.forwarded_body(dict(response["request_body"],
                                               max_output_tokens=32000))
            record = AT.attest(
                chat_body=response["request_body"],
                upstream_body=forwarded,
                parsed=json.loads(response["response_raw"]),
                latency_ms=ms() - started,
                model_expected=MODEL, log=attestation_path,
                raw_ingress=response["request_raw"],
                raw_response=response["response_raw"],
                upstream_capture="reconstructed_from_client_mapping",
                extra={"trial_id": trial_id, "series_id": series_id, "arm": arm})
            item = {
                "arm": arm,
                "text": response["content"],
                "completed_ms": ms(),
                "tool_evidence": T2.TIER,
                "attestation_record_sha256": record["record_sha256"],
                "model_observed": response.get("model"),
                "model_expected": MODEL,
                "response_sha256": record["response"]["sha256"],
                "request_sha256": record["ingress"]["sha256"],
                "terminal_response": response.get("finish_reason") == "stop",
                "retried_ambiguously": False,
                "tool_artifacts": record["response"].get("tool_items") or []
            }
            if item["tool_artifacts"]:
                raise SeriesError("unexpected_tool_artifact:" + arm)
            results.append(item)
            T2.mark_attempt(con, trial_id=trial_id, arm=arm, state="completed",
                            now_ms=item["completed_ms"], session_id=rid)
        return results
    return execute


def _singleton():
    """Acquire nonblocking local exclusive lock before any model-related work.

    This is defense-in-depth; SQLite claims/limits remain the authority.
    """
    import contextlib
    import fcntl
    import os
    @contextlib.contextmanager
    def hold():
        if ROOT.is_symlink():
            raise SeriesError("series_root_is_symlink")
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(ROOT / "SERIES_RUN.lock"),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SeriesError("series_run_already_active") from None
            yield
        finally:
            os.close(fd)
    return hold()


def execute_one():
    act = approved()
    if DB.is_symlink():
        raise SeriesError("series_db_is_symlink")
    with closing(store()) as con:
        c = G.counters(con)
        if c["sealed"] >= G.SERIES_LIMIT or c["calls_reserved"] >= G.CALL_LIMIT:
            return {"status": "budget_exhausted", "counts": c}
        result = choose(con, ms())
        if result is None:
            return {"status": "no_eligible", "counts": c}
        event, semantic = result
        if not isinstance(event["event_id"], str) or len(event["event_id"]) != 64:
            raise SeriesError("invalid_event_digest")
        trial_id = "evt-" + event["event_id"]

        # Mirror original Tier-2 preflight exactly; no new sources, thresholds
        # or prompts. Then revalidate at the actual decision timestamp.
        timestamp = ms()
        cutoff = P.decision_cutoff(timestamp)
        market = P.fetch_market("live", cutoff)
        A.audit_market_payload(market, cutoff_ms=cutoff, now_ms=timestamp)
        prompts = A.build_prompts_3(
            event, market, max_abs_bps=int(max(abs(T2.BPS_RANGE[0]),
                                                abs(T2.BPS_RANGE[1]))))
        meta = {arm: A.execution_metadata(arm, event_ms=cutoff,
                                         model_id=MODEL)
                for arm in A.ARM_NAMES}
        A.audit_market_arm_isolation(prompts, meta, event)
        decision = ms()
        T2.validate_event(event, decision,
                          published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]] * 1000)
        approved()
        G.claim(con, event_id=event["event_id"],
                semantic_cluster_id=semantic, trial_id=trial_id, at_ms=decision)
        try:
            # An input is frozen before the very first network call.
            frozen = {
                "trial_id": trial_id, "series_id": act["series_id"],
                "event": event, "decision_ms": decision, "cutoff_ms": cutoff,
                "market": market, "prompts": prompts,
                "system_prompt": T2.ARM_SYSTEM_T2,
                "system_prompt_sha256":
                    hashlib.sha256(T2.ARM_SYSTEM_T2.encode("utf-8")).hexdigest(),
                "prompt_version": T2.PROMPTS_VERSION,
                "protocol_sha256": protocol_sha()
            }
            freeze_sha = F._write_new_json(
                ROOT / ("frozen-" + trial_id + ".json"), frozen)
            def gate_check():
                current = approved()
                if current["series_id"] != act["series_id"]:
                    raise SeriesError("activation_switched")
                return dict(current, trial_id=trial_id)
            out = O.run_trial(
                con=con, event=event, prompts=prompts,
                executor=model_executor(event_id=event["event_id"],
                                        series_id=act["series_id"],
                                        attestation_path=ATTEST),
                quote_fn=F._quote_after, now_ms=decision,
                trial_id=trial_id, attestation_path=ATTEST,
                gate_check=gate_check,
                published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]] * 1000,
                frozen_sha256=freeze_sha, cohort_extra="",
                protocol_file_sha256=protocol_sha()
            )
            if out["status"] != "sealed":
                raise SeriesError("orchestrator_not_sealed")
            G.finalize(con, event_id=event["event_id"], state="sealed")
            return {"status": "sealed", "event_id": event["event_id"],
                    "due_ms": out["due_ms"], "counts": G.counters(con)}
        except BaseException as exc:
            # No replay of this claimed event. Even one failed model call costs
            # its slot and keeps all raw artifacts for independent review.
            try:
                # A failed freeze/preflight before ANY model invocation is
                # definitely aborted; uncertain in-flight calls remain unknown.
                used = con.execute(
                    "SELECT COUNT(*) FROM series_invocations WHERE event_id=?",
                    (event["event_id"],)).fetchone()[0]
                final_state = "aborted" if used == 0 else "unknown"
                G.finalize(con, event_id=event["event_id"], state=final_state,
                           reason=(type(exc).__name__ + ":" + str(exc))[:180])
            except Exception as final_error:
                print("series_claim_finalize_failed:" + type(final_error).__name__
                      + ":" + str(final_error)[:180], file=sys.stderr, flush=True)
            raise


def settle_labels():
    if not DB.is_file() or DB.is_symlink():
        return {"status": "no_series"}
    t = ms()
    # Label work stays possible after trial is disarmed, to preserve due labels.
    result = LW.settle(db_path=DB, now_ms=t,
                       candle_fn=lambda *, due_ms: LW.exchange_candle(
                           due_ms=due_ms, now_ms=t),
                       dry=False)
    with closing(G.open_db(DB)) as con:
        rows = con.execute(
            "SELECT event_id,label_state,observed_ms FROM tier2_outcomes "
            "WHERE label_state IN ('labelled','censored')").fetchall()
        for event_id, state, observed in rows:
            if not con.execute("SELECT 1 FROM series_labels WHERE event_id=?",
                               (event_id,)).fetchone():
                G.record_label(con, event_id=event_id, state=state,
                               at_ms=observed)
        return {"status": result["status"], "counts": G.counters(con),
                "outcomes": result["outcomes"]}


def status():
    if not DB.is_file():
        return {"status": "not_initialized", "armed": False}
    con = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        c = G.counters(con)
    finally:
        con.close()
    try:
        act = approved()
        armed, state = True, act["series_id"]
    except (G.Refused, SeriesError, OSError):
        armed, state = False, "not_armed"
    return {"status": state, "armed": armed, "counts": c}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true",
                       help="one event; REQUIRES separate approved series GO")
    group.add_argument("--label", action="store_true",
                       help="settle due market labels; NO model calls")
    args = ap.parse_args(argv)
    try:
        if args.execute:
            approved()
            with _singleton():
                result = execute_one()
        else:
            result = settle_labels() if args.label else status()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "refused", "error": type(exc).__name__,
                          "reason": str(exc)[:240]}, sort_keys=True))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

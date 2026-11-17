#!/usr/bin/env python3
"""NEWS Tier-2: fail-closed one-shot live adapter, authored by ChatGPT.

No background process and no implicit activation. By default this program
only inspects a local SQLite snapshot. --execute also requires BOTH existing
Tier-2 gate files, their matching activation record, and a fresh event backed
by an archived raw feed response. --label-once requires a sealed triple and a
closed Binance 1m candle; network failure does not invent an outcome.

This module deliberately does not create activation flags, change the
strict-v2.2 pause, start any daemon, or submit trading orders.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import re
from contextlib import closing
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import v21_paper_prep as P
import v21_pilot_cycle as PC
import v22_arms as A
import tier2 as T2
import tier2_trigger as TR

FEED_DB = Path.home() / ".local/share/news-phase4-v24/v24.sqlite"
MAX_FEED_BYTES = 2 * 1024 * 1024


class DriverError(RuntimeError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _archive_verified(raw_path, expected_digest):
    p = Path(raw_path)
    if not p.is_file() or p.is_symlink():
        raise DriverError("raw_feed_missing_or_symlink")
    if p.stat().st_size > MAX_FEED_BYTES:
        raise DriverError("raw_feed_oversize")
    if digest(p.read_bytes()) != expected_digest:
        raise DriverError("raw_feed_digest_mismatch")
    return True


def candidate(con, now_ms):
    """Read only an occurrence with verified source bytes; never use old
    legacy_no_raw records, undated status data, or a future timestamp."""
    query = """
    SELECT e.content_sha256, e.source_id, e.category, e.provenance,
           e.title, e.origin_url, e.published_ms, e.observed_ms,
           e.raw_sha256, o.raw_path, m.path, m.http_status,
           o.raw_sha256, o.observed_ms, m.source_id, m.observed_ms
    FROM v24_events e
    JOIN v24_observations o ON o.occurrence_id=e.content_sha256
    JOIN v24_raw_manifest m ON m.raw_sha256=e.raw_sha256
                           AND m.source_id=e.source_id
    ORDER BY e.published_ms DESC, e.content_sha256 ASC
    """
    rows = con.execute(query).fetchall()
    for row in rows:
        (event_id, source, cohort, provenance, title, origin_url, pub, obs,
         raw_sha, path, manifest_path, status,
         obs_raw_sha, obs_recorded_ms, manifest_source, manifest_ms) = row
        # Historical records may acquire a later RSS archive when their feed is
        # re-fetched. That is NOT evidence of the bytes observed originally.
        if (not path or path != manifest_path or status != 200
                or manifest_source != source or obs_raw_sha != raw_sha
                or obs_recorded_ms != obs or manifest_ms != obs):
            continue
        event = {
            "event_id": event_id, "source": source, "cohort": cohort,
            "declared_cohort": cohort, "source_kind": provenance,
            "title": title, "summary": "", "origin_url": origin_url,
            "published_ms": pub, "observed_ms": obs,
            "cutoff_ms": P.decision_cutoff(now_ms),
        }
        budget = T2.FRESHNESS_BUDGET_S.get(cohort)
        if budget is None:
            continue
        try:
            T2.validate_event(event, now_ms,
                              published_max_ms=budget * 1000)
        except T2.Tier2Error:
            continue
        _archive_verified(path, raw_sha)
        return event
    return None


def _write_new_json(path, data):
    """Never overwrite an evidence snapshot, even after a crashed trial."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = (json.dumps(data, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        raise
    return digest(raw)


def _quote_after(*, after_ms):
    quote = dict(PC.public_quote(after_ms=after_ms))
    quote["symbol"] = "BTCUSDT"
    return quote


def execute_once(*, feed_db=FEED_DB, now_ms=None, dry=True):
    """One invocation max. No model call before explicit activation AND
    successful raw, time, input, and market-snapshot checks."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if dry:
        with closing(sqlite3.connect(f"file:{Path(feed_db).resolve()}?mode=ro",
                                     uri=True)) as feed:
            found = candidate(feed, now_ms)
        return {"status": "eligible_observed" if found else "no_eligible",
                "event_id": found["event_id"] if found else None,
                "model_calls": 0}
    act = TR.require_enabled()
    with closing(sqlite3.connect(f"file:{Path(feed_db).resolve()}?mode=ro",
                                 uri=True)) as feed:
        event = candidate(feed, now_ms)
    if event is None:
        return {"status": "no_eligible", "model_calls": 0}
    cutoff = P.decision_cutoff(now_ms)
    market = P.fetch_market("live", cutoff)
    A.audit_market_payload(market, cutoff_ms=cutoff, now_ms=now_ms)
    prompts = A.build_prompts_3(event, market)
    meta = {arm: A.execution_metadata(arm, event_ms=cutoff,
                                      model_id="deepseek-v4-flash")
            for arm in A.ARM_NAMES}
    A.audit_market_arm_isolation(prompts, meta, event)
    # This full exact input is frozen BEFORE any model call; hash alone is
    # insufficient for reproducing the original inference.
    frozen = {"trial_id": act["trial_id"], "event": event,
              "decision_ms": now_ms, "cutoff_ms": cutoff,
              "market": market, "prompts": prompts,
              "prompt_version": T2.PROMPTS_VERSION,
              "protocol_sha256": T2.protocol_sha()}
    trial_id = act["trial_id"]
    if not isinstance(trial_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trial_id):
        raise DriverError("invalid_trial_id")
    evidence = T2.DATA / ("frozen-" + trial_id + ".json")
    try:
        freeze_sha = _write_new_json(evidence, frozen)
    except FileExistsError:
        if not evidence.is_file() or evidence.is_symlink():
            raise DriverError("freeze_collision_not_regular_file") from None
        # An earlier attempt may have started calls. Report the immutable
        # evidence and do not retry or claim zero cumulative invocations.
        return {"status": "freeze_already_exists", "trial_id": trial_id,
                "freeze_sha256": digest(evidence.read_bytes())}
    with closing(T2.open_tier2(T2.DEFAULT_DB)) as db:
        result = TR.on_new_event(
            con=db, event=event, prompts=prompts,
            executor=T2.live_executor(
                model="deepseek-v4-flash",
                attestation_path=T2.DEFAULT_ATTESTATION),
            quote_fn=_quote_after, now_ms=now_ms,
            trial_id=act["trial_id"],
            attestation_path=T2.DEFAULT_ATTESTATION,
            published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]] * 1000)
    return {"status": result["status"], "event_id": event["event_id"],
            "trial_id": act["trial_id"], "freeze_sha256": freeze_sha,
            "due_ms": result.get("due_ms")}


def _label_failure_kind(exc):
    """Only known unavailable/unfinished Binance candles are retryable.

    A malformed candle, schema bug or programming exception must be reported
    as an error, not hidden forever behind a generic pending_retry status.
    """
    if isinstance(exc, P.PrepError):
        message = str(exc)
        if message in ("label_pending_candle_open", "label_candle_unavailable"):
            return "pending_retry"
        if message.startswith("label_fetch_failed:"):
            return "pending_retry"
    return "error"


def label_once(*, db_path=T2.DEFAULT_DB, now_ms=None, settle=False):
    """Delegate to the canonical watcher. This function MUST NOT re-implement it.

    The previous version called P.resolve_exit itself, built a candle dict of its
    own (open_ms = exit_ms-59999, closed = exit_ms < now), and only then called
    TR.label_once. Three consequences, all measured: the candle was a reshaped
    dict rather than an exchange observation; a failure inside resolve_exit never
    reached the retry ledger, so no censor was ever possible; and settlement
    logic existed in two places that could drift apart.

    Settlement now lives in exactly one place, tier2_label_watch.settle(), which
    reads the candle from the exchange payload and counts failures in one
    transaction. This wrapper keeps the old call signature for existing callers
    and defaults to dry, like the watcher.
    """
    import tier2_label_watch as LW

    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        return LW.settle(db_path=db_path, now_ms=now_ms, dry=not settle,
                         candle_fn=lambda *, due_ms: LW.exchange_candle(
                             due_ms=due_ms, now_ms=now_ms))
    except LW.LabelWatchError as exc:
        # Preserve this function's long-standing status vocabulary: callers and
        # tests already distinguish "the database is not there" from a refusal.
        if str(exc) == "no_tier2_db":
            return {"status": "no_tier2_db"}
        return {"status": "refused", "reason": str(exc)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--execute", action="store_true",
                   help="ONE gated paper triple on a genuine fresh event")
    g.add_argument("--label-once", action="store_true",
                   help="settle due triples; never issue a model request")
    ap.add_argument("--feed-db", default=str(FEED_DB))
    ap.add_argument("--tier2-db", default=str(T2.DEFAULT_DB))
    args = ap.parse_args(argv)
    try:
        if args.label_once:
            result = label_once(db_path=args.tier2_db, now_ms=None,
                                settle=True)
        else:
            result = execute_once(feed_db=args.feed_db,
                                  dry=not args.execute)
    except Exception as exc:
        result = {"status": "refused", "reason": type(exc).__name__,
                  "detail": str(exc)}
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get("status") in ("error", "freeze_already_exists") else 0


if __name__ == "__main__":
    raise SystemExit(main())

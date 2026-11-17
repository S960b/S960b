#!/usr/bin/env python3
"""Bounded poller for the one-triple Tier-2 observational paper trial.

This file NEVER creates an activation flag, service, cron job or daemon.
One tick is the default. --watch performs a bounded sequence of polls and exits.
The default mode is dry (zero model invocations, read-only SQLite).
Live --execute requires the existing independent Tier-2 activation and trigger
flags BEFORE even beginning to poll. All per-trial limits live in the executor.
"""
import argparse
import json
import sys
import time

import tier2_forward_driver as D
import tier2_trigger as TR
import tier2 as T2

MAX_DURATION_S = 12 * 3600
MIN_INTERVAL_S = 15
MAX_INTERVAL_S = 600
STOP_STATUSES = frozenset(("sealed", "freeze_already_exists", "refused", "error"))


class WatchError(RuntimeError):
    pass


def verify_bounds(*, duration_s, interval_s, execute):
    if (isinstance(duration_s, bool) or isinstance(interval_s, bool)
            or not isinstance(duration_s, int)
            or not isinstance(interval_s, int)):
        raise WatchError("watch_bounds_must_be_int")
    if duration_s <= 0 or duration_s > MAX_DURATION_S:
        raise WatchError("watch_duration_out_of_bounds")
    if not (MIN_INTERVAL_S <= interval_s <= MAX_INTERVAL_S):
        raise WatchError("watch_interval_out_of_bounds")
    if execute:
        # Missing flags are a hard refusal, not an invitation to create them.
        active = TR.require_enabled()
        if active.get("max_triples") != 1 or active.get("max_model_invocations") != 3:
            raise WatchError("activation_limits_not_one_triple")
    return True


def watch(*, feed_db=D.FEED_DB, execute=False, duration_s=3600,
          interval_s=60, single=False, clock=None, sleeper=None, emit=None):
    """Returns on one sealed triple, any refusal, or deadline.

    The live code executes D.execute_once only on explicit --execute and the
    immutable gated trial record. Never retry a failed/ambiguous model attempt.
    The collector continues independently and is never started or stopped here.
    """
    verify_bounds(duration_s=duration_s, interval_s=interval_s, execute=execute)
    clock = clock or time.monotonic
    sleeper = sleeper or time.sleep
    emit = emit or (lambda obj: print(json.dumps(obj, sort_keys=True), flush=True))
    deadline = clock() + duration_s
    ticks = 0
    last = None
    while True:
        if execute:
            TR.require_enabled()  # second check before each polling cycle
        now_ms = time.time_ns() // 1_000_000
        try:
            out = D.execute_once(feed_db=feed_db, now_ms=now_ms,
                                 dry=not execute)
        except Exception as exc:
            emit({"tick": ticks + 1, "status": "refused",
                  "error": type(exc).__name__, "detail": str(exc)[:200],
                  "model_calls_not_asserted": True})
            return {"status": "refused", "ticks": ticks + 1,
                    "reason": type(exc).__name__}
        ticks += 1
        last = dict(out)
        emit({"tick": ticks, "status": out.get("status"),
              "event_id": out.get("event_id"),
              "mode": "paper_trial" if execute else "dry_readonly",
              "observed_ms": now_ms})
        status = out.get("status")
        if status in STOP_STATUSES or status not in (
                "no_eligible", "eligible_observed"):
            return {"status": status or "unknown", "ticks": ticks,
                    "last": last}
        # An eligible status is terminal even if a future driver emits it in
        # execute mode; never poll a second event after a positive detection.
        if single or status == "eligible_observed":
            return {"status": status, "ticks": ticks, "last": last}
        remaining = deadline - clock()
        if remaining <= 0:
            return {"status": "deadline", "ticks": ticks, "last": last}
        sleeper(min(float(interval_s), remaining))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--feed-db", default=str(D.FEED_DB))
    ap.add_argument("--execute", action="store_true",
                    help="gated one-triple paper trial; NO real orders")
    ap.add_argument("--watch", action="store_true",
                    help="poll for at most --duration-s; no daemon")
    ap.add_argument("--duration-s", type=int, default=3600)
    ap.add_argument("--interval-s", type=int, default=60)
    args = ap.parse_args(argv)
    try:
        out = watch(feed_db=args.feed_db, execute=args.execute,
                    single=not args.watch, duration_s=args.duration_s,
                    interval_s=args.interval_s)
    except Exception as exc:
        print(json.dumps({"status": "refused",
                          "error": type(exc).__name__,
                          "detail": str(exc)[:200]}, sort_keys=True))
        return 1
    print(json.dumps({"summary": out}, sort_keys=True))
    return 0 if out["status"] in (
        "no_eligible", "eligible_observed", "deadline", "sealed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

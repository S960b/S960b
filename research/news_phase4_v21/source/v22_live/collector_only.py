#!/usr/bin/env python3
"""NEWS v2.4 — unattended, bounded, collector-ONLY capture loop.

Hard rules encoded here, not documented elsewhere:

  * collector ONLY: this process imports no model client and makes no inference
    call. A prospective triple is impossible from this code path by construction;
  * hard deadline: the loop stops at `--max-hours` regardless of ticks;
  * singleton: an existing live pid in the pid file refuses a second start;
  * kill switch: creating the stop file ends the loop within one poll interval;
  * no daemon, no autostart, no systemd unit: the process dies with its deadline
    and leaves nothing behind except its log;
  * per-tick JSON line appended to a durable log, flushed per line so a crash
    loses at most the tick in flight;
  * timestamps stay separate: `published_ms` (from the feed) never equals
    `observed_ms` (when we fetched), and freshness is computed at decision time.

Run: python3 collector_only.py --max-hours 12 --interval-s 180
"""
import argparse
import json
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

import v24_adapters as AD
import v24_collector as COL

DATA = Path.home() / ".local/share/news-phase4-v24"
DB = DATA / "v24.sqlite"
PIDFILE = DATA / "collector.pid"
STOP_FILE = DATA / "COLLECTOR_STOP"
LOG = DATA / "collector-only.log"

BUDGET_S = {"crypto": 120, "geopolitics": 600, "regulatory": 900,
            "monetary_policy": 300, "macro": 300, "sanctions": 900,
            "energy": 900}

EXIT_STOP = 5
EXIT_ALREADY = 4
EXIT_DEADLINE = 6


class SingletonRefused(RuntimeError):
    pass


def live_pid(pidfile=PIDFILE):
    """Return the pid in the file if that process is really alive, else None."""
    path = Path(pidfile)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except (ValueError, OSError):
        return None
    if pid <= 0:
        return None
    if Path(f"/proc/{pid}").exists():
        return pid
    return None


def claim_singleton(pidfile=PIDFILE, pid=None):
    """Write our pid, or raise if another live instance holds the file."""
    pidfile = Path(pidfile)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    other = live_pid(pidfile)
    if other is not None and other != (pid or os.getpid()):
        raise SingletonRefused(f"already_running:{other}")
    pidfile.write_text(str(pid or os.getpid()))
    return pid or os.getpid()


def release(pidfile=PIDFILE, pid=None):
    """Remove the pid file only if it is ours (a crash of another run must not
    let us delete its lock)."""
    pidfile = Path(pidfile)
    try:
        holder = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        return False
    if holder != (pid or os.getpid()):
        return False
    pidfile.unlink(missing_ok=True)
    return True


def stop_requested(stop_file=STOP_FILE):
    return Path(stop_file).exists()


def freshness(con, now_ms):
    """Best qualifying event at decision time, or None. Never mutates anything."""
    rows = con.execute(
        "SELECT content_sha256, source_id, category, title, origin_url, "
        "published_ms, observed_ms FROM v24_events "
        "ORDER BY published_ms DESC").fetchall()
    for r in rows:
        budget = BUDGET_S.get(r[2], 600)
        age_s = (now_ms - r[5]) // 1000
        if age_s <= budget:
            return {"event_id": r[0], "source": r[1], "category": r[2],
                    "title": r[3][:120], "origin_url": r[4],
                    "published_ms": r[5], "observed_ms": r[6],
                    "age_s": age_s, "budget_s": budget}
    return None


def log_line(obj, log=LOG):
    line = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    with Path(log).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    print(line, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-hours", type=float, default=12.0)
    ap.add_argument("--interval-s", type=int, default=180)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--pidfile", default=str(PIDFILE))
    ap.add_argument("--stop-file", default=str(STOP_FILE))
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--max-ticks", type=int, default=0,
                    help="0 = only the hard deadline limits the run")
    a = ap.parse_args(argv)

    try:
        pid = claim_singleton(a.pidfile)
    except SingletonRefused as exc:
        log_line({"event": "singleton_refused", "detail": str(exc)}, a.log)
        return EXIT_ALREADY

    started_ms = int(time.time() * 1000)
    deadline_ms = started_ms + int(a.max_hours * 3_600_000)

    def _on_signal(signum, _frame):
        log_line({"event": "signal", "signal": signum, "pid": pid,
                  "elapsed_s": (int(time.time() * 1000) - started_ms) // 1000},
                 a.log)
        release(a.pidfile, pid)
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)

    log_line({"event": "start", "pid": pid, "started_ms": started_ms,
              "deadline_ms": deadline_ms, "max_hours": a.max_hours,
              "interval_s": a.interval_s, "mode": "collector_only",
              "db": a.db}, a.log)

    limiter = AD.RateLimiter()
    con = COL.open_db(a.db)
    tick = 0
    last_qualifying = None
    try:
        while True:
            if stop_requested(a.stop_file):
                log_line({"event": "stop_file_seen", "tick": tick, "pid": pid},
                         a.log)
                return EXIT_STOP
            now_ms = int(time.time() * 1000)
            if now_ms >= deadline_ms:
                log_line({"event": "deadline", "tick": tick, "pid": pid,
                          "elapsed_s": (now_ms - started_ms) // 1000}, a.log)
                return EXIT_DEADLINE
            if a.max_ticks and tick >= a.max_ticks:
                log_line({"event": "max_ticks", "tick": tick, "pid": pid},
                         a.log)
                return EXIT_DEADLINE

            tick += 1
            try:
                counts = COL.tick(con=con, limiter=limiter)
            except Exception as exc:                    # a tick must never kill the loop
                log_line({"event": "tick_error", "tick": tick, "pid": pid,
                          "error": type(exc).__name__}, a.log)
                counts = {"error": type(exc).__name__}

            after = int(time.time() * 1000)
            q = freshness(con, after)
            new_total = sum(v.get("new", 0) for v in counts.values()
                            if isinstance(v, dict))
            log_line({"event": "tick", "tick": tick, "pid": pid,
                      "at_ms": after, "new_total": new_total,
                      "counts": counts,
                      "qualifying": None if q is None else
                      {k: q[k] for k in ("source", "category", "age_s",
                                         "budget_s", "title")}}, a.log)
            if q is not None and last_qualifying != q["event_id"]:
                last_qualifying = q["event_id"]
                # Written for the record only. No model is called and no triple
                # is created: this process is collector-only by construction.
                log_line({"event": "qualifying_seen", "tick": tick, "pid": pid,
                          "event": q}, a.log)

            remaining_ms = deadline_ms - int(time.time() * 1000)
            if remaining_ms <= 0:
                continue
            sleep_s = min(a.interval_s, remaining_ms / 1000.0)
            end = time.time() + sleep_s
            while time.time() < end:
                if stop_requested(a.stop_file):
                    log_line({"event": "stop_file_seen", "tick": tick,
                              "pid": pid, "during_sleep": True}, a.log)
                    return EXIT_STOP
                time.sleep(min(2.0, end - time.time()))
    finally:
        con.close()
        release(a.pidfile, pid)
        log_line({"event": "exit", "tick": tick, "pid": pid,
                  "elapsed_s": (int(time.time() * 1000) - started_ms) // 1000},
                 a.log)


if __name__ == "__main__":
    raise SystemExit(main())

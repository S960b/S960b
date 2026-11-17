#!/usr/bin/env python3
"""Bounded freshness watch — one turn, no daemon.

Polls the allowlisted feeds a small number of times and stops as soon as an
event lands inside its category freshness budget. It writes the candidate event
to /tmp/gigatool/event_qualified.json and exits 0. If nothing qualifies within
the tick budget it exits 3 and stores nothing.

This is deliberately NOT a background service: the owner has not authorised a
continuous collector, so nothing here survives the process.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import v24_adapters as AD
import v24_collector as COL

DB = Path.home() / ".local/share/news-phase4-v24/v24.sqlite"
BUDGET = {"crypto": 120, "geopolitics": 600, "regulatory": 900,
          "monetary_policy": 300, "macro": 300, "sanctions": 900,
          "energy": 900}
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
SLEEP_S = int(sys.argv[2]) if len(sys.argv) > 2 else 180
OUT = Path("/tmp/gigatool/event_qualified.json")
PIDFILE = Path.home() / ".local/share/news-phase4-v24/collector.pid"
STOP_FILE = Path.home() / ".local/share/news-phase4-v24/COLLECTOR_STOP"
LOG = Path.home() / ".local/share/news-phase4-v24/collector-trial.log"
MAX_RUNTIME_S = 30 * 60


def single_instance():
    """One instance only: an existing live pid means we refuse to start."""
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    if PIDFILE.exists():
        try:
            other = int(PIDFILE.read_text().strip())
        except ValueError:
            other = None
        if other and Path(f"/proc/{other}").exists():
            print(f"ALREADY_RUNNING pid={other}", flush=True)
            sys.exit(4)
    PIDFILE.write_text(str(os.getpid()))
    return os.getpid()


def stopped():
    return STOP_FILE.exists()

pid = single_instance()
started_ms = int(time.time() * 1000)
deadline_ms = started_ms + MAX_RUNTIME_S * 1000
limiter = AD.RateLimiter()
con = COL.open_db(DB)

def log(obj):
    line = json.dumps(obj, sort_keys=True)
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


for i in range(1, TICKS + 1):
    if stopped():
        log({"event": "stop_file_seen", "tick": i, "pid": pid})
        sys.exit(5)
    if int(time.time() * 1000) > deadline_ms:
        log({"event": "deadline_reached", "tick": i, "pid": pid})
        break
    counts = COL.tick(con=con, limiter=limiter)
    now = int(time.time() * 1000)
    rows = con.execute(
        "SELECT content_sha256, source_id, category, title, origin_url, "
        "published_ms, observed_ms FROM v24_events "
        "ORDER BY published_ms DESC").fetchall()
    best = None
    for r in rows:
        budget = BUDGET.get(r[2], 600)
        age_s = (now - r[5]) // 1000
        if age_s <= budget:
            best = (r, age_s, budget)
            break
    log({"tick": i, "pid": pid, "counts": counts, "now_ms": now,
                      "qualifying": None if best is None else {
                          "source": best[0][1], "category": best[0][2],
                          "age_s": best[1], "budget_s": best[2],
                          "title": best[0][3][:80]}})
    if best is not None:
        r = best[0]
        event = {"event_id": r[0], "source": r[1], "cohort": r[2],
                 "title": r[3], "origin_url": r[4],
                 "published_ms": r[5], "observed_ms": r[6], "summary": ""}
        OUT.write_text(json.dumps(event, ensure_ascii=False, indent=1))
        log({"event": "qualified", "out": str(OUT), "pid": pid})
        sys.exit(0)
    if i < TICKS:
        time.sleep(SLEEP_S)

log({"event": "no_qualifying_story", "pid": pid, "ticks": TICKS,
     "elapsed_s": (int(time.time() * 1000) - started_ms) // 1000})
PIDFILE.unlink(missing_ok=True)
sys.exit(3)

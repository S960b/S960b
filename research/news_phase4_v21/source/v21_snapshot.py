#!/usr/bin/env python3
"""NEWS v2.1 — reproducible audit snapshot.

Writes ONE JSON file containing exactly what an auditor needs and nothing that
could leak secrets: table schemas, counts, integrity checks, the sealed records
(no prompts, no rationale text), and per-session usage totals. Read-only against
every database it opens.
"""
import argparse
import json
import sqlite3
import time
import hashlib
from pathlib import Path

DATA = Path.home() / ".local/share/news-phase4-v21"
TABLES = ("paper_records", "paper_arms", "paper_entries", "paper_outcomes")


def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    return con


def schema(con, table):
    row = con.execute("SELECT sql FROM sqlite_master WHERE name=?",
                      (table,)).fetchone()
    return row[0] if row else None


def digest(payload):
    body = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def records(con):
    out = []
    for row in con.execute(
            "SELECT event_id,cohort,source,published_ms,observed_ms,cutoff_ms,"
            "reserved_ms,completed_ms,provider_id,model_id,seal_sha256,"
            "synthetic,state FROM paper_records ORDER BY completed_ms"):
        out.append(dict(zip(
            ("event_id", "cohort", "source", "published_ms", "observed_ms",
             "cutoff_ms", "reserved_ms", "completed_ms", "provider_id",
             "model_id", "seal_sha256", "synthetic", "state"), row)))
    arms = []
    for row in con.execute(
            "SELECT event_id,arm,attempt_id,prompt_sha256,p_up,"
            "expected_return_bps,uncertainty,action,response_sha256,"
            "completed_ms FROM paper_arms ORDER BY event_id,arm"):
        arms.append(dict(zip(
            ("event_id", "arm", "attempt_id", "prompt_sha256", "p_up",
             "expected_return_bps", "uncertainty", "action",
             "response_sha256", "completed_ms"), row)))
    entries = []
    for row in con.execute(
            "SELECT event_id,price,observed_ms,source FROM paper_entries"):
        entries.append(dict(zip(("event_id", "price", "observed_ms", "source"),
                                row)))
    # Column sets differ between schema versions: an outcome written before
    # due_ms/drift_ms existed has no such columns, and the snapshot must not
    # pretend otherwise.
    cols = [r[1] for r in con.execute("PRAGMA table_info(paper_outcomes)")]
    outcomes = [dict(zip(cols, row)) for row in con.execute(
        "SELECT " + ",".join(cols) + " FROM paper_outcomes "
        "ORDER BY event_id,horizon_h")]
    payload_schema = {"paper_outcomes_columns": cols}
    out_payload = {"records": out, "arms": arms, "entries": entries,
                   "outcomes": outcomes}
    out_payload.update(payload_schema)
    return out_payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    base = Path(a.data_dir)
    payload = {"kind": "news_v21_audit_snapshot", "version": 1,
               "taken_ms": int(time.time() * 1000), "data_dir": str(base),
               "databases": {}}

    paper = base / "paper.sqlite"
    if paper.is_file():
        con = ro(paper)
        try:
            integrity = con.execute("PRAGMA quick_check").fetchone()[0]
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in TABLES
                      if con.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                     (t,)).fetchone()}
            payload["databases"]["paper"] = {
                "quick_check": integrity, "counts": counts,
                "schema": {t: schema(con, t) for t in TABLES},
                **records(con)}
        finally:
            con.close()

    dispatch = base / "dispatch.sqlite"
    if dispatch.is_file():
        con = ro(dispatch)
        try:
            payload["databases"]["dispatch"] = {
                "quick_check": con.execute("PRAGMA quick_check").fetchone()[0],
                "reservations": con.execute(
                    "SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0],
                "queue": con.execute(
                    "SELECT COUNT(*) FROM dispatch_queue").fetchone()[0],
                "lease": con.execute(
                    "SELECT holder,acquired_ms,expires_ms,synthetic "
                    "FROM dispatch_lease WHERE id=1").fetchone(),
                "by_event": [dict(zip(("event_id", "arms", "state"),
                                      r)) for r in con.execute(
                    "SELECT event_id,COUNT(*),MIN(state) "
                    "FROM dispatch_reservations GROUP BY event_id")],
            }
        finally:
            con.close()

    events = base / "v21.sqlite"
    if events.is_file():
        con = ro(events)
        try:
            payload["databases"]["events"] = {
                "quick_check": con.execute("PRAGMA quick_check").fetchone()[0],
                "total": con.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "by_cohort": dict(con.execute(
                    "SELECT cohort,COUNT(*) FROM events GROUP BY cohort")
                    .fetchall()),
                "by_source": dict(con.execute(
                    "SELECT source,COUNT(*) FROM events GROUP BY source")
                    .fetchall()),
            }
        finally:
            con.close()

    payload["snapshot_sha256"] = digest(
        {k: v for k, v in payload.items() if k != "snapshot_sha256"})
    text = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=True)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"status": "written", "path": a.out,
                          "sha256": payload["snapshot_sha256"]}))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

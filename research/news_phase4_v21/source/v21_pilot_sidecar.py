#!/usr/bin/env python3
"""Mark pilot pairs as exploratory WITHOUT touching the sealed records.

The sealed journal is immutable by contract; this writes a separate sidecar
file. Every pilot pair is listed with the reason it is not audit-grade, and
with the tool invocations observed in its sessions, because a browsable model
could have read prices after the cutoff.
"""
import argparse
import json
import sqlite3
from pathlib import Path

DATA = Path.home() / ".local/share/news-phase4-v21"
GATEWAY = Path.home() / ".local/share/gigatool/opencode.db"


def tool_calls(event_id, gateway):
    """Tool names and a short query, for one event's two sessions."""
    if not gateway.is_file():
        return None
    con = sqlite3.connect(f"file:{gateway}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            "SELECT s.title, p.data FROM part p JOIN session s "
            "ON s.id=p.session_id WHERE s.title LIKE ?",
            (f"%{event_id[:12]}%",)).fetchall()
    finally:
        con.close()
    out = []
    for title, data in rows:
        part = json.loads(data) if isinstance(data, str) else data
        if part.get("type") != "tool":
            continue
        state = part.get("state") or {}
        query = str((state.get("input") or {}).get("query", ""))[:80]
        out.append({"session": title, "tool": part.get("tool"),
                    "query": query})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--gateway-db", default=str(GATEWAY))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    base = Path(a.data_dir)
    paper = base / "paper.sqlite"
    if not paper.is_file():
        print(json.dumps({"status": "no_records"}))
        return 0
    con = sqlite3.connect(f"file:{paper}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    try:
        rows = con.execute(
            "SELECT event_id,cohort,source,completed_ms,synthetic,state "
            "FROM paper_records ORDER BY completed_ms").fetchall()
        reserved = con.execute(
            "SELECT event_id,MIN(reserved_ms) FROM dispatch_reservations "
            "GROUP BY event_id").fetchall() if False else []
    finally:
        con.close()

    dcon_path = base / "dispatch.sqlite"
    if dcon_path.is_file():
        d = sqlite3.connect(f"file:{dcon_path}?mode=ro", uri=True, timeout=5)
        try:
            d.execute("PRAGMA query_only=ON")
            reserved = d.execute(
                "SELECT event_id,MIN(reserved_ms) FROM dispatch_reservations "
                "GROUP BY event_id").fetchall()
        finally:
            d.close()

    recorded = {r[0] for r in rows}
    pilot = []
    for event_id, cohort, source, completed, synthetic, state in rows:
        tool = tool_calls(event_id, Path(a.gateway_db))
        reasons = ["pre_tool_policy", "single_pair"]
        if tool:
            reasons.append("tool_access_present")
        pilot.append({
            "event_id": event_id, "cohort": cohort, "source": source,
            "completed_ms": completed, "synthetic": synthetic,
            "state": state, "classification": "PILOT_DISCOVERY",
            "reasons": reasons, "tool_calls": tool,
            "audit_usable": False,
        })
    orphans = [{"event_id": e, "reserved_ms": ms, "reason":
                "reserved_but_never_recorded", "classification": "PILOT_DISCOVERY",
                "audit_usable": False}
               for e, ms in reserved if e not in recorded]
    payload = {"kind": "news_v21_pilot_sidecar", "version": 1,
               "note": ("Pilot data only. Not audit-grade: the tool policy of "
                        "NEWS_V21_AUDIT_PROTOCOL.md section 2 was not in force."),
               "pilot_pairs": pilot, "unrecorded_reservations": orphans}
    text = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=True)
    target = Path(a.out) if a.out else (base / "pilot_sidecar.json")
    target.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "path": str(target),
                      "pilot": len(pilot), "orphans": len(orphans)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

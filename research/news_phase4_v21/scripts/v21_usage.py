#!/usr/bin/env python3
"""Read-only usage reconciliation for NEWS v2.1.

Three numbers that must never be conflated again:
  * reservations - our research ledger (one per arm per pair);
  * completions  - what the gateway actually billed (a multi-step answer is
                   several assistant messages inside ONE reservation);
  * tokens/cost  - input, output and the gateway's own USD estimate.

Nothing here writes to the gateway database; the connection is read-only.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

GATEWAY_DB = Path.home() / ".local/share/gigatool/opencode.db"


def connect(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    return con


def session_usage(con, title_like="NEWS v2.1%"):
    """Per-session usage, read from the gateway's own message rows."""
    rows = con.execute(
        "SELECT id,title,time_created FROM session WHERE title LIKE ? "
        "ORDER BY time_created", (title_like,)).fetchall()
    out = []
    for sid, title, created in rows:
        totals = {"input": 0, "output": 0, "reasoning": 0, "cost": 0.0,
                  "completions": 0, "tools": 0, "provider": None, "model": None}
        for (data,) in con.execute(
                "SELECT data FROM message WHERE session_id=?", (sid,)):
            msg = json.loads(data) if isinstance(data, str) else data
            if msg.get("role") != "assistant":
                continue
            totals["completions"] += 1
            tok = msg.get("tokens") or {}
            totals["input"] += int(tok.get("input") or 0)
            totals["output"] += int(tok.get("output") or 0)
            totals["reasoning"] += int(tok.get("reasoning") or 0)
            totals["cost"] += float(msg.get("cost") or 0.0)
            totals["provider"] = msg.get("providerID") or totals["provider"]
            totals["model"] = msg.get("modelID") or totals["model"]
        for (data,) in con.execute(
                "SELECT data FROM part WHERE session_id=?", (sid,)):
            part = json.loads(data) if isinstance(data, str) else data
            if part.get("type") == "tool":
                totals["tools"] += 1
        totals.update({"session_id": sid, "title": title, "created_ms": created})
        out.append(totals)
    return out


def summarise(sessions):
    by_model = {}
    allt = {"compl": 0, "input": 0, "output": 0, "cost": 0.0, "tools": 0}
    for s in sessions:
        key = f"{s['provider']}/{s['model']}"
        agg = by_model.setdefault(key, {"completions": 0, "input": 0,
                                        "output": 0, "cost": 0.0, "tools": 0})
        agg["completions"] += s["completions"]
        agg["input"] += s["input"]
        agg["output"] += s["output"]
        agg["cost"] += s["cost"]
        agg["tools"] += s["tools"]
        allt["compl"] += s["completions"]
        allt["input"] += s["input"]
        allt["output"] += s["output"]
        allt["cost"] += s["cost"]
        allt["tools"] += s["tools"]
    for agg in by_model.values():
        agg["cost"] = round(agg["cost"], 6)
    allt["cost"] = round(allt["cost"], 6)
    return {"by_model": by_model, "total": allt}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway-db", default=str(GATEWAY_DB))
    ap.add_argument("--sessions", action="store_true",
                    help="list every session, not just the totals")
    ap.add_argument("--title-like", default="NEWS v2.1%")
    a = ap.parse_args(argv)
    if not Path(a.gateway_db).is_file():
        print(json.dumps({"status": "unavailable",
                          "reason": "gateway_db_missing"}))
        return 2
    con = connect(a.gateway_db)
    try:
        sessions = session_usage(con, a.title_like)
    finally:
        con.close()
    payload = {"status": "ok", "sessions": len(sessions),
               **summarise(sessions)}
    if a.sessions:
        payload["detail"] = sessions
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

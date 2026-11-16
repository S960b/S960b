#!/usr/bin/env python3
"""Sanitised, read-only export for the public audit bundle.

Emits ONE JSON with everything an auditor needs and nothing they must not see:
no prompts, no rationale text, no gateway database, no credentials, no full
article bodies. News items keep only source, id-free timestamps, a truncated
title and the canonical URL, so an outsider can verify the event existed.
"""
import argparse
import json
import sqlite3
import time
from pathlib import Path

DATA = Path.home() / ".local/share/news-phase4-v21"
GATEWAY = Path.home() / ".local/share/gigatool/opencode.db"
TITLE_MAX = 180


def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    return con


def sanitise_title(text):
    if not text:
        return None
    clean = " ".join(str(text).split())
    return clean[:TITLE_MAX]


def events(con):
    rows = con.execute(
        "SELECT event_id,source,origin_id,published_ms,observed_ms,source_kind,"
        "cohort,payload_json FROM events ORDER BY published_ms").fetchall()
    out = []
    for eid, source, origin, pub, obs, kind, cohort, payload in rows:
        try:
            body = json.loads(payload or "{}")
        except ValueError:
            body = {}
        out.append({
            "event_id": eid,
            "source": source,
            "published_ms": pub,
            "observed_ms": obs,
            "source_kind": kind,
            "cohort": cohort,
            "title": sanitise_title(body.get("title")),
            "url": body.get("url") or body.get("link"),
            "origin_id_present": bool(origin),
        })
    return out


def paper(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(paper_records)")]
    records = [dict(zip(cols, r)) for r in con.execute(
        "SELECT " + ",".join(cols) + " FROM paper_records ORDER BY completed_ms")]
    arms = [dict(zip(("event_id", "arm", "attempt_id", "session_id",
                      "prompt_sha256", "p_up", "expected_return_bps",
                      "uncertainty", "action", "response_sha256",
                      "completed_ms"), r)) for r in con.execute(
        "SELECT event_id,arm,attempt_id,session_id,prompt_sha256,p_up,"
        "expected_return_bps,uncertainty,action,response_sha256,completed_ms "
        "FROM paper_arms ORDER BY event_id,arm")]
    entries = [dict(zip(("event_id", "price", "observed_ms", "source"), r))
               for r in con.execute(
        "SELECT event_id,price,observed_ms,source FROM paper_entries")]
    ocols = [r[1] for r in con.execute("PRAGMA table_info(paper_outcomes)")]
    outcomes = [dict(zip(ocols, r)) for r in con.execute(
        "SELECT " + ",".join(ocols) + " FROM paper_outcomes")]
    return {"records": records, "arms": arms, "entries": entries,
            "outcomes": outcomes,
            "schema": {t: con.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
                for t in ("paper_records", "paper_arms", "paper_entries",
                          "paper_outcomes")}}


def dispatch(con):
    return {
        "reservations": [dict(zip(("event_id", "arm", "reserved_ms", "state",
                                   "synthetic"), r)) for r in con.execute(
            "SELECT event_id,arm,reserved_ms,state,synthetic "
            "FROM dispatch_reservations ORDER BY reserved_ms")],
        "lease": con.execute(
            "SELECT holder,acquired_ms,expires_ms,synthetic FROM dispatch_lease "
            "WHERE id=1").fetchone(),
        "policy": {"note": "research throttle, not a provider quota"},
    }


def gateway_usage(gw_path):
    """NEWS sessions only: agent, completions, tokens, cost, tool counts."""
    if not gw_path.is_file():
        return {"status": "unavailable", "reason": "gateway_db_missing"}
    con = ro(gw_path)
    try:
        out = []
        rows = con.execute(
            "SELECT id,title,agent,time_created FROM session "
            "WHERE title LIKE 'NEWS v2.1%' ORDER BY time_created").fetchall()
        for sid, title, agent, created in rows:
            completions = tokens_in = tokens_out = tools = 0
            cost = 0.0
            provider = model = None
            for (data,) in con.execute(
                    "SELECT data FROM message WHERE session_id=?", (sid,)):
                msg = json.loads(data) if isinstance(data, str) else data
                if msg.get("role") != "assistant":
                    continue
                completions += 1
                tok = msg.get("tokens") or {}
                tokens_in += int(tok.get("input") or 0)
                tokens_out += int(tok.get("output") or 0)
                cost += float(msg.get("cost") or 0.0)
                provider = msg.get("providerID") or provider
                model = msg.get("modelID") or model
            for (data,) in con.execute(
                    "SELECT data FROM part WHERE session_id=?", (sid,)):
                part = json.loads(data) if isinstance(data, str) else data
                if part.get("type") == "tool":
                    tools += 1
            out.append({"session_title": title, "agent": agent,
                        "created_ms": created, "completions": completions,
                        "tokens_in": tokens_in, "tokens_out": tokens_out,
                        "cost_usd": round(cost, 6), "tools": tools,
                        "provider": provider, "model": model})
    finally:
        con.close()
    total = {"completions": sum(s["completions"] for s in out),
             "tokens_in": sum(s["tokens_in"] for s in out),
             "tokens_out": sum(s["tokens_out"] for s in out),
             "cost_usd": round(sum(s["cost_usd"] for s in out), 6),
             "tools": sum(s["tools"] for s in out)}
    return {"status": "ok", "sessions": out, "total": total}


def attempt_reconciliation(paper, dispatch, usage, refusal_rows):
    """Per-event ledger with an explicit evidence level.

    Never invents a trace. Two kinds of refusal exist in this project:
      * `trace`         - a row in finish-refusals.jsonl on disk;
      * `reconstructed` - no trace was written (the tracer did not exist yet),
                          so the attempt is inferred from the reservation rows,
                          the gateway sessions and the absence of a record.
    The distinction is published because it changes what an auditor may conclude.
    """
    records = {r["event_id"]: r for r in paper.get("records", [])}
    traced = {r["event_id"]: r for r in refusal_rows}
    sessions = {}
    for s in usage.get("sessions", []):
        for eid in records:
            if eid[:12] in s["session_title"]:
                sessions.setdefault(eid, []).append(s)
    grouped = {}
    for r in dispatch.get("reservations", []):
        g = grouped.setdefault(r["event_id"], {"arms": 0,
                                               "first_ms": r["reserved_ms"]})
        g["arms"] += 1
        g["first_ms"] = min(g["first_ms"], r["reserved_ms"])
    out = []
    for event_id, g in sorted(grouped.items(), key=lambda kv: kv[1]["first_ms"]):
        rec = records.get(event_id)
        trace = traced.get(event_id)
        if rec:
            status, evidence = "recorded", "trace"
        elif trace:
            status, evidence = "refused", "trace"
        else:
            status, evidence = "refused", "reconstructed"
        out.append({
            "event_id": event_id,
            "attempted_ms": g["first_ms"],
            "arm_reservations": g["arms"],
            "status": status,
            "refusal_reason": (trace or {}).get("reason"),
            "evidence_level": evidence,
            "recorded": bool(rec),
            "synthetic": rec.get("synthetic") if rec else None,
            "cohort": rec.get("cohort") if rec else None,
        })
    return out


def refusals(path):
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rec.pop("tail", None)          # never publish model prose
        rec["arms"] = [{k: a.get(k) for k in ("arm", "model", "len")}
                       for a in rec.get("arms", [])]
        out.append(rec)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--gateway-db", default=str(GATEWAY))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    base = Path(a.data_dir)
    payload = {"kind": "news_v21_public_export", "version": 1,
               "taken_ms": int(time.time() * 1000),
               "timezone": "all timestamps are milliseconds since epoch, UTC"}

    ep = base / "v21.sqlite"
    if ep.is_file():
        con = ro(ep)
        try:
            payload["events"] = {"quick_check": con.execute(
                "PRAGMA quick_check").fetchone()[0], "rows": events(con)}
        finally:
            con.close()

    pp = base / "paper.sqlite"
    if pp.is_file():
        con = ro(pp)
        try:
            payload["paper"] = {"quick_check": con.execute(
                "PRAGMA quick_check").fetchone()[0], **paper(con)}
        finally:
            con.close()

    dp = base / "dispatch.sqlite"
    if dp.is_file():
        con = ro(dp)
        try:
            payload["dispatch"] = {"quick_check": con.execute(
                "PRAGMA quick_check").fetchone()[0], **dispatch(con)}
        finally:
            con.close()

    usage = gateway_usage(Path(a.gateway_db))
    payload["gateway_usage"] = usage
    refusal_rows = refusals(base / "finish-refusals.jsonl")
    payload["refusals"] = refusal_rows
    payload["attempt_reconciliation"] = attempt_reconciliation(
        payload.get("paper", {}), payload.get("dispatch", {}), usage,
        refusal_rows)
    Path(a.out).write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"status": "written", "path": a.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

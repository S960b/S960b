#!/usr/bin/env python3
"""Tool-availability preflight: prove DENIAL, not good behaviour.

Requesting a tool and seeing the model simply not use it proves nothing. This
probe asks the model to use a tool *explicitly*, then reads the gateway's own
record: a refused call leaves a tool part with status "error" and a permission
refusal message, while an allowed call leaves status "running"/"completed".

Exit codes: 0 = denial proven, 3 = tool executed (leak), 4 = inconclusive.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

GATEWAY = Path.home() / ".local/share/gigatool/opencode.db"
PROBE_TEXT = (
    "Use the websearch tool right now to search for 'BTCUSDT price'. "
    "This is a permission test: if the tool is unavailable or refused, reply "
    'with exactly {"tool_test":"refused"} and nothing else. If you did run the '
    'search, reply with exactly {"tool_test":"allowed"}.'
)


def latest_probe_session(con, since_ms):
    row = con.execute(
        "SELECT id FROM session WHERE title LIKE 'NEWS v2.1 toolpreflight%' "
        "AND time_created>=? ORDER BY time_created DESC LIMIT 1",
        (since_ms,)).fetchone()
    return row[0] if row else None


def inspect(con, session_id):
    tools, texts = [], []
    for (data,) in con.execute(
            "SELECT data FROM part WHERE session_id=?", (session_id,)):
        part = json.loads(data) if isinstance(data, str) else data
        if part.get("type") == "tool":
            state = part.get("state") or {}
            tools.append({"tool": part.get("tool"),
                          "status": state.get("status"),
                          "error": str(state.get("error") or "")[:200]})
        elif part.get("type") == "text" and part.get("text", "").strip():
            texts.append(part["text"].strip())
    return {"tools": tools, "texts": texts}


def verdict(report):
    """GO requires positive technical evidence, never good manners.

    Absence of a tool call proves nothing: a cooperative model may simply
    decline. So does a bare error status - a network failure arrives AFTER the
    tool was already exposed and possibly executed. Proof requires one of:
      * a tool part whose error names a permission refusal (the call was
        blocked by policy), or
      * absence of the tool from the request the model was given, which this
        probe cannot observe from the gateway and therefore never claims.

    Anything else is INCONCLUSIVE, which is a fail-closed verdict.
    """
    DENIAL = ("permission", "denied", "not allowed", "forbidden",
              "tool not available", "unknown tool", "not enabled")
    refused, executed, other_errors = [], [], []
    for t in report["tools"]:
        status = str(t.get("status") or "")
        err = (t.get("error") or "").lower()
        if status in ("running", "completed"):
            executed.append(t)
        elif status == "error" and any(w in err for w in DENIAL):
            refused.append(t)
        elif status == "error":
            other_errors.append(t)
    if executed:
        return 3, "tool_executed", executed
    if refused:
        return 0, "denial_proven_permission_refused", refused
    if other_errors:
        return 4, "inconclusive_tool_error_not_a_permission_refusal", other_errors
    # No tool part at all: the model may have chosen not to call it. Never a pass.
    return 4, "inconclusive_no_tool_attempt_recorded", None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--request-file", required=True,
                    help="file the live plugin watches to run the probe")
    ap.add_argument("--result-file", required=True,
                    help="file the plugin writes the probe result to")
    ap.add_argument("--gateway-db", default=str(GATEWAY))
    ap.add_argument("--timeout", type=float, default=90.0)
    a = ap.parse_args(argv)

    started = int(time.time() * 1000)
    req = Path(a.request_file)
    res = Path(a.result_file)
    if res.exists():
        res.unlink()
    req.write_text(json.dumps({"nonce": f"toolpreflight-{started}",
                               "text": PROBE_TEXT}), encoding="utf-8")

    deadline = time.time() + a.timeout
    while time.time() < deadline and not res.exists():
        time.sleep(2)
    if not res.exists():
        print(json.dumps({"status": "blocked", "reason": "probe_not_answered",
                          "model_called": False}))
        return 4

    payload = json.loads(res.read_text(encoding="utf-8"))
    session_id = payload.get("session_id")
    gateway = Path(a.gateway_db)
    if not session_id or not gateway.is_file():
        print(json.dumps({"status": "blocked",
                          "reason": "no_session_or_gateway",
                          "probe": payload}))
        return 4

    con = sqlite3.connect(f"file:{gateway}?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        report = inspect(con, session_id)
    finally:
        con.close()
    code, reason, detail = verdict(report)
    print(json.dumps({"status": "ok" if code == 0 else "failed",
                      "verdict": reason, "exit_code": code,
                      "session_id": session_id,
                      "tools": report["tools"],
                      "model_text": report["texts"][-1][:300] if report["texts"]
                      else None,
                      "detail": detail}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

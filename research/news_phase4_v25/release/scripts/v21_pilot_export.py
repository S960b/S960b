#!/usr/bin/env python3
"""Sanitised export of the one-off PROMPT_ONLY pilot ledger.

Separate from the production export on purpose: a pilot row must never be
mistaken for a prospective production record.
"""
import argparse
import json
import sqlite3
from pathlib import Path

DATA = Path.home() / ".local/share/news-phase4-v21"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    path = Path(a.data_dir) / "pilot.sqlite"
    if not path.is_file():
        Path(a.out).write_text(json.dumps({"status": "absent"}) + "\n",
                               encoding="utf-8")
        print(json.dumps({"status": "absent"}))
        return 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(pilot_pairs)")]
        pairs = [dict(zip(cols, r)) for r in con.execute(
            "SELECT " + ",".join(cols) + " FROM pilot_pairs")]
        acols = [r[1] for r in con.execute("PRAGMA table_info(pilot_arms)")]
        arms = [dict(zip(acols, r)) for r in con.execute(
            "SELECT " + ",".join(acols) + " FROM pilot_arms")]
        ocols = [r[1] for r in con.execute("PRAGMA table_info(pilot_outcomes)")]
        outcomes = [dict(zip(ocols, r)) for r in con.execute(
            "SELECT " + ",".join(ocols) + " FROM pilot_outcomes")]
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    # Stored labels are historical facts and stay untouched. What the export
    # additionally exposes is the classification those rows still support: a
    # pair with no persisted tool provenance has UNKNOWN evidence and can never
    # be presented as compliant, whatever its stored label says.
    states = ("known_zero", "known_positive", "unknown")
    for arm in arms:
        if arm.get("tool_evidence") not in states:
            arm["tool_evidence"] = "unknown"
            arm.setdefault("tool_evidence_provenance", "absent")
            arm["tool_evidence_derived"] = True
        else:
            arm["tool_evidence_derived"] = False
    per_pair = {}
    for arm in arms:
        per_pair.setdefault(arm["event_id"], []).append(arm)
    for pair in pairs:
        evs = per_pair.get(pair["event_id"], [])
        pos = [e.get("tool_evidence") for e in evs]
        if not evs or any(s == "unknown" for s in pos):
            eff = "PILOT_TOOL_EVIDENCE_UNKNOWN"
        elif any(s == "known_positive" for s in pos):
            eff = "PILOT_PROMPT_ONLY_TOOL_LEAK"
        elif not all(x.get("session_verified") == 1 for x in evs):
            eff = "PILOT_SESSION_UNVERIFIED"
        else:
            eff = "PROMPT_ONLY_COMPLIANT_ONE_PAIR"
        pair["label_stored"] = pair.get("label")
        pair["label_effective"] = eff
        pair["evidence_version"] = (evs[0].get("evidence_version") if evs
                                    else None)
    payload = {"kind": "news_v21_prompt_only_pilot", "version": 2,
               "quick_check": quick, "pairs": pairs, "arms": arms,
               "outcomes": outcomes,
               "note": ("PILOT ONLY. prompt-only-pilot-v1 header; the tool ban is "
                        "an instruction, not enforcement. Never pool with "
                        "prospective data.")}
    Path(a.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": "written", "pairs": len(pairs)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only Phase4 v2 milestone snapshot; no network, SQLite writes or secrets.

- Forecast milestone: exactly two completed N/NM decisions + post-inference quote.
- Outcome milestone: at least one mature 4h label, evaluated as paired paper study.
- A reserved/incomplete pair is NOT automatically retried.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

from v2_core import POLICY_VERSION, V2Error, require
from v2_cycle import CONTRACT_SHA, study_evaluation, _pending
from v2_recovery import present as recovery_present
from v2_early_recovery import present as early_present

ROOT=Path.home()/".local/share/news-phase4-v2"
HOUR=3_600_000
LABEL_BUFFER=120_000


def snapshot(root=ROOT, now_ms=None):
    if now_ms is None:
        now_ms=time.time_ns()//1_000_000
    require(type(now_ms) is int and now_ms>0, "invalid_clock")
    path=Path(root).expanduser().absolute()/"v2.sqlite"
    if path.is_symlink() or not path.is_file():
        return {"status":"not_initialized", "read_only":True}
    con=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        policy=con.execute("SELECT v FROM meta WHERE k='policy'").fetchone()
        contract=con.execute("SELECT v FROM meta WHERE k='contract_sha'").fetchone()
        require(policy==(POLICY_VERSION,) and contract==(CONTRACT_SHA,),
                "unexpected_cohort_policy")
        counts={}
        for name,table in (("pairs","pairs"),("attempts","attempts"),
                           ("decisions","decisions"),("quotes","entry_quotes"),
                           ("labels","outcomes")):
            counts[name]=con.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
        recovery=recovery_present(con)
        counts["abandoned_pairs"]=(con.execute(
            "SELECT COUNT(*) FROM abandoned_pairs").fetchone()[0]
            if recovery else 0)
        early=early_present(con)
        counts["abandoned_early_pairs"]=(con.execute(
            "SELECT COUNT(*) FROM abandoned_early_pairs").fetchone()[0]
            if early else 0)
        counts["abandoned_total"]=(counts["abandoned_pairs"]+
                                   counts["abandoned_early_pairs"])
        counts["pending_pairs"]=_pending(con)
        counts["mature_4h_labels"]=con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE horizon_h=4").fetchone()[0]
        counts["complete_pairs"]=con.execute("""
            SELECT COUNT(*) FROM entry_quotes q WHERE
             (SELECT COUNT(*) FROM attempts a JOIN decisions d USING(attempt_id)
               WHERE a.event_id=q.event_id)=2
        """).fetchone()[0]
        exclusions=[]
        if recovery:
            exclusions.append("NOT EXISTS (SELECT 1 FROM abandoned_pairs a "
                              "WHERE a.event_id=p.event_id)")
        if early:
            exclusions.append("NOT EXISTS (SELECT 1 FROM abandoned_early_pairs x "
                              "WHERE x.event_id=p.event_id)")
        fixed_filter=(" WHERE "+" AND ".join(exclusions)) if exclusions else ""
        # Only constant, locally authored SQL fragments are concatenated.
        row=con.execute("""
            SELECT p.event_id,p.reserved_ms,q.entry_ms,
            (SELECT COUNT(*) FROM attempts a JOIN decisions d USING(attempt_id)
              WHERE a.event_id=p.event_id) AS complete,
            (SELECT COUNT(*) FROM outcomes o WHERE o.event_id=p.event_id
              AND o.horizon_h=4) AS score
            FROM pairs p LEFT JOIN entry_quotes q ON q.event_id=p.event_id
        """+fixed_filter+"""
            ORDER BY p.reserved_ms ASC,p.event_id ASC LIMIT 1
        """).fetchone()
        if row is None:
            milestone=("waiting_next_forward_event_after_abandonment"
                       if counts["abandoned_total"] else "waiting_first_forward_event")
            first=None
        else:
            eid,reserved,entry,complete,scored=row
            due=entry+4*HOUR+LABEL_BUFFER if entry is not None else None
            first={"event_prefix":eid[:12], "decisions":complete,
                   "post_inference_quote":entry is not None,
                   "entry_ms":entry,
                   "four_hour_label_due_ms":due,
                   "four_hour_label_recorded":bool(scored)}
            if scored:
                milestone="first_4h_result_recorded"
            elif entry is not None and complete==2:
                milestone="first_forecast_pair_recorded"
            else:
                milestone="incomplete_pair_requires_monitoring"
            if milestone=="incomplete_pair_requires_monitoring" and \
                    now_ms-reserved>30*60_000:
                milestone="incomplete_pair_requires_manual_reconciliation"
        evaluation=study_evaluation(con)
        # Evaluator returns aggregate numeric information only, no news/body.
        return {"status":"ok","read_only":True,"now_ms":now_ms,
                "milestone":milestone,"counts":counts,"first_pair":first,
                "evaluation":evaluation}
    finally:
        con.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=ROOT)
    args=p.parse_args(argv)
    try:
        print(json.dumps(snapshot(args.root),sort_keys=True,
                         separators=(",",":"),allow_nan=False))
        return 0
    except (V2Error,sqlite3.Error,OSError,ValueError,TypeError):
        print('{"status":"error","code":"v2_monitor_fail_closed"}',file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())

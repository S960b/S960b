#!/usr/bin/env python3
"""NEWS v2.6-only strict label policy; leave the v2.5 Tier-2 watcher unchanged.

A +4h label is terminal exactly once. Expired records are censored even when
Binance later returns a valid price; unexpected errors are recorded as FAILED,
not silently left pending. A broken archived market witness is censored with an
explicit source diagnostic. All mutations are isolated to the v2.6 series DB.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import tier2_label_watch as LW


class V26LabelError(RuntimeError):
    pass


def _terminal(con, event_id, *, due_ms, now_ms, state, reason):
    if state not in ("censored","failed"):
        raise V26LabelError("bad_terminal_state")
    source=(str(reason) or state)[:160]
    con.execute("BEGIN IMMEDIATE")
    try:
        current=LW.existing_outcome(con,event_id)
        if current is not None:
            con.commit()
            return {"event_id":event_id,"status":current,"already":True}
        con.execute(
            "INSERT INTO tier2_outcomes(event_id,due_ms,price,observed_ms,"
            "source,drift_ms,label_state) VALUES(?,?,?,?,?,?,?)",
            (event_id,int(due_ms),0.0,int(now_ms),source,-1,state))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return {"event_id":event_id,"status":state,"reason":source}


def settle(*, db_path, now_ms, symbol_fn, candle_fn, verify_fn,
           max_failures=LW.MAX_FAILURES,max_wait_ms=LW.MAX_WAIT_MS):
    """Strict isolated v2.6 label pass; no activation or model calls.

    verified market witness required BEFORE calling Binance. For seal failures
    in the shared Tier-2 integrity layer, leave DB unchanged and surface error.
    """
    if type(now_ms) is not int or now_ms<=0 or max_wait_ms<=0:
        raise V26LabelError("invalid_label_clock")
    p=Path(db_path)
    if p.is_symlink() or not p.is_file():
        raise V26LabelError("series_db_missing_or_symlink")
    with closing(sqlite3.connect(str(p),timeout=10,isolation_level=None)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        rows=LW.sealed_rows(con)
        outcomes=[]
        for row in rows:
            eid,due=row["event_id"],int(row["due_ms"])
            previous=LW.existing_outcome(con,eid)
            if previous is not None:
                outcomes.append({"event_id":eid,"status":previous,"already":True})
                continue
            if int(now_ms)<due:
                outcomes.append({"event_id":eid,"status":"pending",
                                 "reason":"not_due_yet"})
                continue
            # Absolute, deterministic deadline. A late successful response
            # must never revive a record that should already be censored.
            if int(now_ms)>due+max_wait_ms:
                outcomes.append(_terminal(con,eid,due_ms=due,now_ms=now_ms,
                    state="censored",reason="label_deadline_expired"))
                continue
            first_close=(due//60_000)*60_000+59_999
            if now_ms<=first_close:
                outcomes.append({"event_id":eid,"status":"pending",
                                 "reason":"await_first_closed_candle"})
                continue
            try:
                symbol=symbol_fn(con,eid)
                verify_fn(con,event_id=eid,symbol=symbol)
            except Exception as exc:
                # Broken source artifacts are not a legitimate priced label.
                # Configuration/programming errors must be separately flagged.
                reason=type(exc).__name__+":"+str(exc)[:120]
                state=("censored" if "market_witness" in str(exc) else "failed")
                outcomes.append(_terminal(con,eid,due_ms=due,now_ms=now_ms,
                                          state=state,reason=reason))
                continue
            try:
                result=LW.settle_one(
                    con,row,now_ms=now_ms,candle_fn=candle_fn,dry=False,
                    symbol_fn=symbol_fn,max_failures=max_failures,
                    max_wait_ms=max_wait_ms)
            except Exception as exc:
                result={"event_id":eid,"status":"error",
                        "reason":type(exc).__name__+":"+str(exc)[:120]}
            if result["status"]=="error":
                result=_terminal(con,eid,due_ms=due,now_ms=now_ms,
                                 state="failed",reason=result.get("reason","bug"))
            outcomes.append(result)
        return {
            "status":"checked","dry":False,"outcomes":outcomes,
            "failed":sum(x["status"]=="failed" for x in outcomes),
            "censored":sum(x["status"]=="censored" for x in outcomes),
            "labelled":sum(x["status"]=="labelled" for x in outcomes)
        }


def selftest():
    """No live model, exchange, DB or inputs; test terminal transaction only."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db=Path(tmp)/"test.sqlite"
        with closing(sqlite3.connect(db,isolation_level=None)) as con:
            con.executescript("""
                CREATE TABLE tier2_outcomes(
                 event_id TEXT PRIMARY KEY,due_ms INTEGER,price REAL,
                 observed_ms INTEGER,source TEXT,drift_ms INTEGER,label_state TEXT);
            """)
            first=_terminal(con,"e"*64,due_ms=100,now_ms=102,
                            state="failed",reason="injected_bug")
            second=_terminal(con,"e"*64,due_ms=100,now_ms=104,
                             state="censored",reason="late")
            assert first["status"]=="failed"
            assert second["status"]=="failed" and second["already"]
            assert con.execute("SELECT COUNT(*) FROM tier2_outcomes").fetchone()[0]==1
    print("NEWS V26 LABEL POLICY SELFTEST OK: terminal failure idempotent")


if __name__=="__main__":
    selftest()

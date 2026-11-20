#!/usr/bin/env python3
"""NEWS v2.6 read-only local monitoring: BTC/ETH/SOL paper series and RSS14.

Standalone report; no HTTP service, no model calls, no DB schema mutations.
Never confuses absent DB with a healthy zero-event DB. The real deployment
must separately validate its systemd file and WAL shared-memory access.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

SYMBOLS = ("BTCUSDT","ETHUSDT","SOLUSDT")
MAX_EVENTS = 100
MAX_CALLS = 300


def read_db(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return None
    conn = sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=3)
    conn.execute("PRAGMA query_only=ON")
    return conn


def grouped(con, sql, params=()):
    return {k:int(v) for k,v in con.execute(sql,params).fetchall()}


def report(*, series_path, feeds_path, now_ms=None):
    if now_ms is None:
        now_ms = time.time_ns()//1_000_000
    if type(now_ms) is not int or now_ms <= 0:
        raise ValueError("invalid_monitor_clock")
    summary={
        "version":"v26-monitor-v1","paper_only":True,"now_ms":now_ms,
        "series":{"state":"absent","max_events":MAX_EVENTS,"max_calls":MAX_CALLS},
        "sources":{"state":"absent"},"errors":[]
    }
    try:
        series=read_db(series_path)
    except sqlite3.Error as exc:
        summary["errors"].append("series_db:"+type(exc).__name__)
        summary["series"]["state"]="error"
        series=None
    if series is not None:
        try:
            claims=grouped(series,
                "SELECT state,COUNT(*) FROM series_claims GROUP BY state")
            calls=series.execute("SELECT COUNT(*) FROM series_invocations").fetchone()[0]
            seals=series.execute(
                "SELECT COUNT(*) FROM tier2_triples").fetchone()[0]
            labels=grouped(series,
                "SELECT label_state,COUNT(*) FROM tier2_outcomes "
                "GROUP BY label_state")
            assets={}
            for symbol in SYMBOLS:
                assets[symbol]={
                    "claimed":0,"sealed":0,"labelled":0,"censored":0,
                    "failed":0,
                    "reserved_calls":0,"witness_hourly":0,
                    "witness_entry":0,"witness_exit":0
                }
            for symbol,n in series.execute(
                "SELECT symbol,COUNT(*) FROM series_asset_bindings "
                "GROUP BY symbol"):
                if symbol not in assets:
                    raise ValueError("unknown_binding_symbol")
                assets[symbol]["claimed"]=int(n)
            for symbol,n in series.execute(
                "SELECT b.symbol,COUNT(*) FROM series_asset_bindings b "
                "JOIN series_claims c ON c.event_id=b.event_id "
                "WHERE c.state='sealed' GROUP BY b.symbol"):
                assets[symbol]["sealed"]=int(n)
            for symbol,state,n in series.execute(
                "SELECT b.symbol,o.label_state,COUNT(*) "
                "FROM series_asset_bindings b JOIN tier2_outcomes o "
                "ON o.event_id=b.event_id GROUP BY b.symbol,o.label_state"):
                if state in ("labelled","censored","failed"):
                    assets[symbol][state]=int(n)
            for symbol,n in series.execute(
                "SELECT b.symbol,COUNT(*) FROM series_asset_bindings b "
                "JOIN series_invocations i ON i.event_id=b.event_id "
                "GROUP BY b.symbol"):
                assets[symbol]["reserved_calls"]=int(n)
            for symbol,stage,n in series.execute(
                "SELECT symbol,operation,COUNT(*) "
                "FROM series_market_witnesses GROUP BY symbol,operation"):
                assets[symbol]["witness_"+stage]=int(n)
            summary["series"]={
                "state":"observed","max_events":MAX_EVENTS,"max_calls":MAX_CALLS,
                "claims_by_state":claims,"sealed_rows":int(seals),
                "reserved_calls":int(calls),
                "calls_left":max(0,MAX_CALLS-int(calls)),
                "labels_by_state":labels,"assets":assets
            }
            if calls>MAX_CALLS or seals>MAX_EVENTS:
                summary["errors"].append("series_budget_breached")
            if sum(x["reserved_calls"] for x in assets.values()) != calls:
                summary["errors"].append("unbound_invocations")
        except (sqlite3.Error,ValueError,KeyError,TypeError) as exc:
            summary["series"]={"state":"error",
                               "max_events":MAX_EVENTS,"max_calls":MAX_CALLS}
            summary["errors"].append("series_query:"+type(exc).__name__+
                                     ":"+str(exc)[:100])
        finally:
            series.close()
    try:
        feeds=read_db(feeds_path)
    except sqlite3.Error as exc:
        summary["sources"]["state"]="error"
        summary["errors"].append("feed_db:"+type(exc).__name__)
        feeds=None
    if feeds is not None:
        try:
            rows=feeds.execute(
                "SELECT source_id,COUNT(*),MAX(published_ms) "
                "FROM v24_events GROUP BY source_id ORDER BY source_id"
            ).fetchall()
            errors=feeds.execute(
                "SELECT source_id,COUNT(*) FROM v24_fetch_log "
                "WHERE error IS NOT NULL AND at_ms>=? GROUP BY source_id",
                (now_ms-86_400_000,)).fetchall()
            summary["sources"]={
                "state":"observed","count":len(rows),
                "latest_published_age_s":{
                    source:(now_ms-int(pub))//1000
                    for source,_,pub in rows if pub is not None},
                "stored_items":{source:int(count) for source,count,_ in rows},
                "fetch_errors_24h":{source:int(n) for source,n in errors},
                "note":"latest post age is NOT feed publication-to-delivery latency"
            }
        except (sqlite3.Error,ValueError,KeyError,TypeError) as exc:
            summary["sources"]={"state":"error"}
            summary["errors"].append("feed_query:"+type(exc).__name__)
        finally:
            feeds.close()
    return summary


def selftest():
    import tempfile
    import sys
    from pathlib import Path
    here=Path(__file__).resolve().parent
    src=here.parent/"release"/"source"
    live=src/"v22_live"
    for directory in (src,live,here):
        if str(directory) not in sys.path:
            sys.path.insert(0,str(directory))
    import series_guard as G
    import tier2 as T2
    import v24_collector as C
    import v26_asset_binding as B
    from v26_series_runner import MARKET_LINKS_SCHEMA
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        absent=report(series_path=root/"absent.sqlite",
                      feeds_path=root/"none.sqlite",now_ms=1_800_000_000_000)
        assert absent["series"]["state"]=="absent"
        assert absent["sources"]["state"]=="absent"
        assert absent["errors"]==[]
        with T2.open_tier2(root/"series.sqlite"):
            pass
        with G.open_db(root/"series.sqlite") as conn:
            B.install_schema(conn)
            conn.executescript(MARKET_LINKS_SCHEMA)
        with C.open_db(root/"rss.sqlite"):
            pass
        got=report(series_path=root/"series.sqlite",
                   feeds_path=root/"rss.sqlite",now_ms=1_800_000_000_000)
        assert got["series"]["state"]=="observed",got
        assert got["series"]["reserved_calls"]==0
        assert got["series"]["calls_left"]==300
        assert len(got["series"]["assets"])==3
        assert got["sources"]["state"]=="observed"
        assert not got["errors"],got
    print("NEWS V26 MONITOR SELFTEST OK: absent/initialized, BTC/ETH/SOL, zero writes")


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    root=Path.home()/".local/share/news-phase4-v26-series100"
    ap.add_argument("--series-db",default=str(root/"series.sqlite"))
    ap.add_argument("--feeds-db",default=str(root/"rss.sqlite"))
    ap.add_argument("--selftest",action="store_true")
    args=ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    result=report(series_path=args.series_db,feeds_path=args.feeds_db)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0 if not result["errors"] else 2


if __name__=="__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""NEWS v2.6: prospective RSS latency AND candidate-admission diagnostics.

Diagnostic only. A fresh post arriving within 120 seconds may still be
non-material. Never equate RSS delivery speed with an eligible trading signal.
Reads the write-once baseline plus current, independently stored feed/sidecar
databases. No HTTP, model calls, live orders or DB writes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

import v26_rss_arrival_audit as ARRIVAL
import rss_noise_filter as NOISE
import tier2 as T2


class ProbeError(ValueError):
    pass


def _read(path):
    p=Path(path)
    if not p.is_file() or p.is_symlink():
        raise ProbeError("db_missing_or_symlink")
    return sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)


def policy(row, *, at_ms):
    """Classify at FIRST observation, not retroactively at report time."""
    filter_row={
        "source_id":row["source_id"],"title":row["title"],
        "summary":row["summary"],"asset_scope":row["asset_scope"],
        "published_ms":row["published_ms"],
        "first_observed_ms":row["first_observed_ms"],
        "raw_sha256":row["raw_sha256"]
    }
    d=NOISE.decide(filter_row,now_ms=at_ms)
    if not d.accepted:
        return {"candidate":False,"reason":"noise:"+d.reason,"symbol":None}
    event={
        "event_id":row["occurrence_id"],"cohort":row["category"],
        "published_ms":row["published_ms"],
        "observed_ms":row["first_observed_ms"],
    }
    try:
        T2.validate_event(
            event,at_ms,
            published_max_ms=T2.FRESHNESS_BUDGET_S[event["cohort"]]*1000)
    except (T2.Tier2Error,KeyError) as exc:
        return {"candidate":False,
                "reason":"tier2:"+type(exc).__name__+":"+str(exc)[:72],
                "symbol":None}
    return {"candidate":True,"reason":"eligible_at_capture","symbol":d.symbol}


def probe(*,feeds_db,metadata_db,baseline_file,now_ms=None):
    now_ms=time.time_ns()//1_000_000 if now_ms is None else now_ms
    baseline_path=Path(baseline_file)
    if baseline_path.is_symlink() or not baseline_path.is_file():
        raise ProbeError("baseline_missing")
    baseline=json.loads(baseline_path.read_text(encoding="utf-8"))
    ARRIVAL.validate_snapshot(baseline)
    initial=set(baseline["baseline_ids"])
    entries=ARRIVAL.inventory(feeds_db,metadata_db)
    at={r["occurrence_id"]:r for r in entries}
    with _read(feeds_db) as feed, _read(metadata_db) as side:
        rows=side.execute(
            "SELECT m.occurrence_id,m.source_id,m.title,m.summary,m.asset_scope,"
            "m.published_ms,m.first_observed_ms,m.raw_sha256,"
            "e.category,e.provenance "
            "FROM rss_metadata m "
            "JOIN v24_events e ON e.content_sha256=m.occurrence_id "
            "ORDER BY m.first_observed_ms,m.occurrence_id").fetchall()
    fields=("occurrence_id","source_id","title","summary","asset_scope",
            "published_ms","first_observed_ms","raw_sha256",
            "category","provenance")
    summaries=Counter()
    by_source={}
    examples=[]
    for values in rows:
        row=dict(zip(fields,values))
        id=row["occurrence_id"]
        if id not in at or id in initial:
            continue
        observed=row["first_observed_ms"]
        pub=row["published_ms"]
        if observed<=baseline["baseline_ms"]:
            summaries["new_id_first_observed_before_baseline"]+=1
            continue
        summaries["new_occurrences"]+=1
        if pub<=baseline["baseline_ms"]:
            summaries["old_published_newly_observed"]+=1
            continue
        summaries["new_published_after_baseline"]+=1
        lag=observed-pub
        if lag<=120_000:
            summaries["arrived_within_120s"]+=1
        decision=policy(row,at_ms=observed)
        summaries["reason:"+decision["reason"]]+=1
        if decision["candidate"]:
            summaries["potentially_eligible_at_capture"]+=1
        source=by_source.setdefault(row["source_id"],
            {"new_published":0,"within_120s":0,
             "potentially_eligible_at_capture":0,"reasons":{}})
        source["new_published"]+=1
        source["within_120s"]+=int(lag<=120_000)
        source["potentially_eligible_at_capture"]+=int(decision["candidate"])
        source["reasons"][decision["reason"]]=(
            source["reasons"].get(decision["reason"],0)+1)
        if len(examples)<30:
            examples.append({
                "source":row["source_id"],"title":row["title"][:120],
                "published_ms":pub,"first_observed_ms":observed,
                "lag_ms":lag,"eligible_at_capture":decision["candidate"],
                "reason":decision["reason"],"symbol":decision["symbol"]})
    return {
        "version":"v26-arrival-candidate-probe-v1",
        "baseline_ms":baseline["baseline_ms"],"now_ms":now_ms,
        "counts":dict(sorted(summaries.items())),"by_source":by_source,
        "examples":examples,
        "note":"Admission simulated at first recorded observation only; actual decision"
               " may be later. No signal was executed or backdated."
    }


def selftest():
    b=1_800_000_000_000
    mk=lambda title,source="coindesk",scope="MULTI",lag=80_000:{
        "occurrence_id":"a"*64,"source_id":source,"title":title,
        "summary":"","asset_scope":scope,"raw_sha256":"b"*64,
        "published_ms":b,"first_observed_ms":b+lag,
        "category":"crypto","provenance":"secondary_media"}
    fast=policy(mk("Ethereum suffers major exploit"),at_ms=b+80_000)
    assert fast=={"candidate":True,"reason":"eligible_at_capture",
                  "symbol":"ETHUSDT"},fast
    slow=policy(mk("Ethereum suffers major exploit",lag=130_000),
                at_ms=b+130_000)
    assert not slow["candidate"] and slow["symbol"] is None,slow
    irrelevant=policy(mk("Cathie Wood's Ark Invest bought Anthropic IPO shares"),
                      at_ms=b+80_000)
    assert not irrelevant["candidate"],irrelevant
    print("NEWS V26 ARRIVAL CANDIDATE PROBE SELFTEST OK: fast eligible, slow stale, irrelevant")

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    root=Path.home()/".local/share/news-phase4-v26-series100"
    p.add_argument("--feeds-db",default=str(root/"rss.sqlite"))
    p.add_argument("--metadata-db",default=str(root/"rss_meta.sqlite"))
    p.add_argument("--baseline-file",default=str(root/"arrival_baseline.json"))
    p.add_argument("--selftest",action="store_true")
    a=p.parse_args(argv)
    try:
        if a.selftest:
            selftest()
        else:
            print(json.dumps(probe(feeds_db=a.feeds_db,
                                   metadata_db=a.metadata_db,
                                   baseline_file=a.baseline_file),
                             sort_keys=True,ensure_ascii=False))
    except (ProbeError,ARRIVAL.AuditError,sqlite3.Error,OSError,
            ValueError,TypeError) as exc:
        print(json.dumps({"status":"refused","error":type(exc).__name__,
                          "reason":str(exc)[:160]},sort_keys=True))
        return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())

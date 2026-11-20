#!/usr/bin/env python3
"""NEWS v2.6: prospective RSS latency AND candidate-admission diagnostics.

Diagnostic only. A fresh post arriving within 120 seconds may still be
non-material. Never equate RSS delivery speed with an eligible trading signal.
Reads the write-once baseline plus current, independently stored feed/sidecar
databases. No HTTP, model calls, live orders or DB writes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "release" / "source"
LIVE = SOURCE / "v22_live"
RSS = HERE.parent / "rss_expansion"
for directory in (HERE, SOURCE, LIVE, RSS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

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



def joined_rows(*, feeds_db, metadata_db):
    """Read two independent SQLite files; NEVER cross-join in one connection.

    The collector stores v24_events in rss.sqlite and rss_metadata in
    rss_meta.sqlite. Compare occurrence identities and evidence in Python.
    A mismatched/partially committed pair is refused, not called eligible.
    """
    with closing(_read(feeds_db)) as feed, closing(_read(metadata_db)) as side:
        event_rows = feed.execute(
            "SELECT content_sha256,source_id,title,published_ms,observed_ms,"
            "raw_sha256,category,provenance FROM v24_events"
        ).fetchall()
        meta_rows = side.execute(
            "SELECT occurrence_id,source_id,title,summary,asset_scope,"
            "published_ms,first_observed_ms,raw_sha256 FROM rss_metadata"
        ).fetchall()
    events = {r[0]: r for r in event_rows}
    if len(events) != len(event_rows):
        raise ProbeError("duplicate_feed_event_identity")
    found = set()
    joined = []
    for m in meta_rows:
        eid, src, title, summary, scope, pub, observed, sha = m
        if eid in found:
            raise ProbeError("duplicate_metadata_identity")
        found.add(eid)
        event = events.get(eid)
        if event is None:
            raise ProbeError("metadata_without_event")
        if ((src, title, pub, observed, sha) !=
                (event[1], event[2], event[3], event[4], event[5])):
            raise ProbeError("two_db_evidence_mismatch")
        joined.append((*m, event[6], event[7]))
    if found != set(events):
        raise ProbeError("event_without_metadata")
    return sorted(joined, key=lambda r: (r[6], r[0]))


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
    rows = joined_rows(feeds_db=feeds_db, metadata_db=metadata_db)
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
    # Reproduce the REAL deployment topology: metadata and events are in
    # DIFFERENT SQLite database files. A single-db SQL JOIN must fail here.
    with tempfile.TemporaryDirectory() as tmp:
        feed_path = Path(tmp) / "rss.sqlite"
        meta_path = Path(tmp) / "rss_meta.sqlite"
        with closing(sqlite3.connect(feed_path)) as feed, \
             closing(sqlite3.connect(meta_path)) as side:
            feed.execute(
                "CREATE TABLE v24_events (content_sha256 TEXT PRIMARY KEY,"
                "source_id TEXT,title TEXT,published_ms INTEGER,"
                "observed_ms INTEGER,raw_sha256 TEXT,category TEXT,"
                "provenance TEXT)")
            side.execute(
                "CREATE TABLE rss_metadata (occurrence_id TEXT PRIMARY KEY,"
                "source_id TEXT,title TEXT,summary TEXT,asset_scope TEXT,"
                "published_ms INTEGER,first_observed_ms INTEGER,"
                "raw_sha256 TEXT)")
            record=mk("Ethereum suffers major exploit")
            eid=record["occurrence_id"]
            feed.execute(
                "INSERT INTO v24_events VALUES (?,?,?,?,?,?,?,?)",
                (eid,record["source_id"],record["title"],
                 record["published_ms"],record["first_observed_ms"],
                 record["raw_sha256"],record["category"],
                 record["provenance"]))
            side.execute(
                "INSERT INTO rss_metadata VALUES (?,?,?,?,?,?,?,?)",
                (eid,record["source_id"],record["title"],record["summary"],
                 record["asset_scope"],record["published_ms"],
                 record["first_observed_ms"],record["raw_sha256"]))
            feed.commit()
            side.commit()
        two_db=joined_rows(feeds_db=feed_path,metadata_db=meta_path)
        assert len(two_db)==1 and two_db[0][0]==eid,two_db
        assert two_db[0][-2:]==("crypto","secondary_media")
        snap=ARRIVAL.snapshot([],at_ms=b-1)
        baseline=Path(tmp)/"baseline.json"
        baseline.write_text(json.dumps(snap),encoding="utf-8")
        output=probe(feeds_db=feed_path,metadata_db=meta_path,
                     baseline_file=baseline,now_ms=b+100_000)
        assert output["counts"]["potentially_eligible_at_capture"]==1,output
        with closing(sqlite3.connect(meta_path)) as side:
            side.execute(
                "UPDATE rss_metadata SET raw_sha256=? WHERE occurrence_id=?",
                ("c"*64,eid))
            side.commit()
        try:
            joined_rows(feeds_db=feed_path,metadata_db=meta_path)
            raise AssertionError("cross_db_mismatch_accepted")
        except ProbeError as exc:
            assert str(exc)=="two_db_evidence_mismatch",exc
    print("NEWS V26 ARRIVAL CANDIDATE PROBE SELFTEST OK: two SQLite files, eligible, mismatch, stale, irrelevant")

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

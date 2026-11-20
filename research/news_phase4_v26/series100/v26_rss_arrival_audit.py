#!/usr/bin/env python3
"""NEWS v2.6 paper RSS arrival-latency audit, independent of model activation.

The first 14-feed fetch returned mainly archive/backfill items. That snapshot
DOES NOT establish the delivery latency for future RSS posts. Create a
write-once inventory baseline, then compare later observations and report the
true publication-to-first-observation lag ONLY for IDs not in that baseline.

Read-only SQLite queries; --baseline creates one local JSON inventory file.
--report only reads existing files, never changes RSS databases. Does not
activate a model, place orders, scrape articles, or change freshness budgets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from collections import defaultdict
from pathlib import Path

VERSION = "news-v26-rss-arrival-audit-v1"
THRESHOLDS_MS = (120_000, 600_000, 1_800_000, 3_600_000)


class AuditError(ValueError):
    pass


def _shaids(ids):
    return hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode()).hexdigest()


def _read(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        raise AuditError("database_missing_or_symlink")
    return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def inventory(feed_db, metadata_db):
    """Only cross-validated joined RSS occurrences count as observable items."""
    with _read(feed_db) as e, _read(metadata_db) as m:
        event_rows = e.execute(
            "SELECT content_sha256,source_id,title,published_ms,observed_ms,"
            "raw_sha256 FROM v24_events").fetchall()
        meta_rows = m.execute(
            "SELECT occurrence_id,source_id,title,published_ms,"
            "first_observed_ms,raw_sha256 FROM rss_metadata").fetchall()
    events = {r[0]: r for r in event_rows}
    metadata = {r[0]: r for r in meta_rows}
    invalid = []
    records = []
    for eid in set(events) | set(metadata):
        r, s = events.get(eid), metadata.get(eid)
        if r is None or s is None:
            invalid.append((eid, "sidecar_event_unmatched"))
            continue
        if (r[1:4] != s[1:4] or r[4] != s[4] or r[5] != s[5]):
            invalid.append((eid, "sidecar_event_mismatch"))
            continue
        pub, first = r[3], r[4]
        if (type(pub) is not int or type(first) is not int
                or pub <= 0 or pub > first):
            invalid.append((eid, "invalid_publication_clock"))
            continue
        records.append({
            "occurrence_id": eid, "source_id": r[1], "published_ms": pub,
            "first_observed_ms": first, "publication_lag_ms": first - pub
        })
    if invalid:
        by_kind = defaultdict(int)
        for _, kind in invalid:
            by_kind[kind] += 1
        raise AuditError("rss_evidence_mismatch:" + json.dumps(
            dict(by_kind), sort_keys=True))
    return records


def snapshot(records, *, at_ms):
    if type(at_ms) is not int or at_ms <= 0:
        raise AuditError("invalid_baseline_clock")
    ids = [r["occurrence_id"] for r in records]
    if len(ids) != len(set(ids)):
        raise AuditError("duplicate_occurrence_id")
    return {"version":VERSION, "baseline_ms":at_ms,
            "baseline_occurrences":len(ids),
            "baseline_sha256":_shaids(ids),
            "baseline_ids":sorted(ids)}


def _write_new(path, data):
    p = Path(path)
    if p.is_symlink():
        raise AuditError("output_symlink")
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw=(json.dumps(data, sort_keys=True, ensure_ascii=True,
                    separators=(",", ":")) + "\n").encode()
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
               getattr(os,"O_NOFOLLOW",0),0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def validate_snapshot(data):
    if (not isinstance(data,dict) or data.get("version")!=VERSION
            or type(data.get("baseline_ms")) is not int
            or not isinstance(data.get("baseline_ids"),list)
            or not all(isinstance(x,str) and len(x)==64
                       and all(c in "0123456789abcdef" for c in x)
                       for x in data["baseline_ids"])
            or len(set(data["baseline_ids"]))!=len(data["baseline_ids"])
            or data.get("baseline_occurrences")!=len(data["baseline_ids"])
            or data.get("baseline_sha256")!=_shaids(data["baseline_ids"])):
        raise AuditError("baseline_corrupted")
    return True


def _median_int(numbers):
    ordered=sorted(numbers)
    if not ordered:
        return None
    n=len(ordered)
    if n%2:
        return ordered[n//2]
    return (ordered[n//2-1]+ordered[n//2])//2


def _summarize(records):
    lags=[r["publication_lag_ms"] for r in records]
    return {
        "count":len(records),
        "lag_min_ms":min(lags) if lags else None,
        "lag_median_ms":_median_int(lags),
        "lag_max_ms":max(lags) if lags else None,
        "within_120s":sum(x<=120_000 for x in lags),
        "within_600s":sum(x<=600_000 for x in lags),
        "within_30m":sum(x<=1_800_000 for x in lags),
        "within_60m":sum(x<=3_600_000 for x in lags)
    }


def report(data, current, *, now_ms):
    validate_snapshot(data)
    if type(now_ms) is not int or now_ms < data["baseline_ms"]:
        raise AuditError("invalid_reporting_clock")
    before=set(data["baseline_ids"])
    new=[r for r in current if r["occurrence_id"] not in before]
    # First seen after baseline can have a very old publisher date; report
    # that separately rather than misrepresenting it as realtime delivery.
    new_after=[r for r in new if r["first_observed_ms"] > data["baseline_ms"]]
    newly_published=[r for r in new_after
                     if r["published_ms"] > data["baseline_ms"]]
    seen_before=[r for r in current if r["occurrence_id"] in before]
    by_source={}
    for source in sorted({r["source_id"] for r in new_after}):
        group=[r for r in new_after if r["source_id"]==source]
        by_source[source]={
            "since_baseline":_summarize(group),
            "published_after_baseline":_summarize(
                [r for r in group if r["published_ms"]>data["baseline_ms"]])
        }
    return {
        "version":VERSION,"baseline_ms":data["baseline_ms"],
        "now_ms":now_ms,"baseline_count":len(before),
        "current_count":len(current),
        "persisted_baseline_count":len(seen_before),
        "new_occurrences":_summarize(new),
        "new_first_seen_after_baseline":_summarize(new_after),
        "new_published_after_baseline":_summarize(newly_published),
        "by_source":by_source,
        "conclusion":(
            "no_new_post_publications_observed_yet"
            if not newly_published else
            "measured_new_post_publications; examine lag distribution and source"
        ),
        "warning":"Initial archive snapshot is not a prospective feed-latency sample"
    }


def selftest():
    at=1_800_000_000_000
    r1={"occurrence_id":"a"*64,"source_id":"coindesk",
        "published_ms":at-3_600_000,"first_observed_ms":at-1000,
        "publication_lag_ms":3_599_000}
    snap=snapshot([r1],at_ms=at)
    validate_snapshot(snap)
    newfast={"occurrence_id":"b"*64,"source_id":"coindesk",
             "published_ms":at+60_000,"first_observed_ms":at+90_000,
             "publication_lag_ms":30_000}
    newlate={"occurrence_id":"c"*64,"source_id":"coindesk",
             "published_ms":at-3_600_000,"first_observed_ms":at+90_000,
             "publication_lag_ms":3_690_000}
    result=report(snap,[r1,newfast,newlate],now_ms=at+100_000)
    assert result["new_occurrences"]["count"]==2
    assert result["new_first_seen_after_baseline"]["count"]==2
    assert result["new_published_after_baseline"]["count"]==1
    assert result["new_published_after_baseline"]["within_120s"]==1
    assert result["by_source"]["coindesk"]["since_baseline"]["within_120s"]==1
    assert report(snap,[r1],now_ms=at+100_000)["conclusion"]==(
        "no_new_post_publications_observed_yet")
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/"baseline.json"
        _write_new(p,snap)
        assert json.loads(p.read_bytes())==snap
        try:
            _write_new(p,snap)
            raise AssertionError("baseline overwritten")
        except FileExistsError:
            pass
    print("NEWS V26 RSS ARRIVAL AUDIT SELFTEST OK: baseline, new posts, delayed archive")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selftest",action="store_true")
    mode.add_argument("--baseline",action="store_true")
    mode.add_argument("--report",action="store_true")
    p.add_argument("--feeds-db",default=str(Path.home()/
                   ".local/share/news-phase4-v26-series100/rss.sqlite"))
    p.add_argument("--metadata-db",default=str(Path.home()/
                   ".local/share/news-phase4-v26-series100/rss_meta.sqlite"))
    p.add_argument("--baseline-file")
    args=p.parse_args(argv)
    try:
        if args.selftest:
            selftest()
            return 0
        if not args.baseline_file:
            raise AuditError("baseline_file_required")
        if args.baseline:
            data=snapshot(inventory(args.feeds_db,args.metadata_db),
                          at_ms=time.time_ns()//1_000_000)
            _write_new(args.baseline_file,data)
            print(json.dumps({"status":"baseline_saved",
                              "count":data["baseline_occurrences"],
                              "sha256":data["baseline_sha256"]},sort_keys=True))
        else:
            filename=Path(args.baseline_file)
            if filename.is_symlink() or not filename.is_file():
                raise AuditError("baseline_missing_or_symlink")
            data=json.loads(filename.read_text(encoding="utf-8"))
            print(json.dumps(
                report(data,inventory(args.feeds_db,args.metadata_db),
                       now_ms=time.time_ns()//1_000_000),
                ensure_ascii=False,sort_keys=True))
        return 0
    except (AuditError,sqlite3.Error,OSError,ValueError,TypeError) as exc:
        print(json.dumps({"status":"refused","error":type(exc).__name__,
                          "reason":str(exc)[:200]},sort_keys=True))
        return 2


if __name__=="__main__":
    raise SystemExit(main())

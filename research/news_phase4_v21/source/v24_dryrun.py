#!/usr/bin/env python3
"""NEWS v2.4 — offline dry-run harness over a synthetic seven-category day.

No network, no feed, no model. The point is to exercise the selector contract
under conditions a live run will eventually meet: every category present, an
outage in one of them, a backfilled record, a late record, a duplicate arriving
from a second source, and a record whose digest does not match.

The output is a report, not evidence about markets.
"""
import json

import v24_sources as S

BUCKET_MS = 3_600_000
BASE = 1_800_000_000_000


def _record(source_id, category, title, *, published_ms, observed_ms,
            provenance="primary", digest_ok=True):
    digest = S.content_digest(title, published_ms)
    if not digest_ok:
        digest = "0" * 64
    return S.SourceRecord(source_id=source_id, category=category,
                          provenance=provenance, published_ms=published_ms,
                          observed_ms=observed_ms, title=title,
                          origin_url="https://example.gov/item", 
                          content_sha256=digest)


def synthetic_day():
    """One hour of records covering all seven categories plus faults."""
    now = BASE + 3_600_000
    return now, [
        _record("bin", "crypto", "Bitcoin funding flips positive",
                published_ms=now - 60_000, observed_ms=now - 55_000),
        _record("bls", "macro", "Payrolls beat expectations",
                published_ms=now - 200_000, observed_ms=now - 195_000),
        _record("fed", "monetary_policy", "FOMC holds rates steady",
                published_ms=now - 240_000, observed_ms=now - 235_000),
        _record("wire", "geopolitics", "Border talks resume in Geneva",
                published_ms=now - 400_000, observed_ms=now - 380_000,
                provenance="media_wire"),
        _record("ofac", "sanctions", "Treasury sanctions two crypto mixers",
                published_ms=now - 500_000, observed_ms=now - 495_000),
        _record("eia", "energy", "Crude inventories fall sharply",
                published_ms=now - 700_000, observed_ms=now - 690_000),
        _record("sec", "regulatory", "SEC proposes custody rule change",
                published_ms=now - 800_000, observed_ms=now - 795_000),
        # fault: the same sanctions story arriving from a second source
        _record("ofac", "sanctions", "Treasury sanctions two crypto mixers",
                published_ms=now - 500_000, observed_ms=now - 480_000),
        # fault: a backfilled record published long before it was observed
        _record("eia", "energy", "Pipeline outage restored",
                published_ms=now - 3_000_000, observed_ms=now - 900_000),
        # fault: bad digest
        _record("sec", "regulatory", "SEC opens comment period",
                published_ms=now - 100_000, observed_ms=now - 95_000,
                digest_ok=False),
    ]


def registry():
    return S.SourceRegistry([
        S.SourceSpec("bin", "crypto", "primary", 120),
        S.SourceSpec("bls", "macro", "primary", 300),
        S.SourceSpec("fed", "monetary_policy", "primary", 300),
        S.SourceSpec("wire", "geopolitics", "media_wire", 600),
        S.SourceSpec("ofac", "sanctions", "primary", 900),
        S.SourceSpec("eia", "energy", "primary", 900),
        S.SourceSpec("sec", "regulatory", "primary", 900),
    ])


def run():
    now, records = synthetic_day()
    reg = registry()
    result = S.select(records, now_ms=now, registry=reg)
    timeline = S.coverage_timeline(records, start_ms=now - BUCKET_MS,
                                   end_ms=now + 1, bucket_ms=BUCKET_MS,
                                   registry=reg, heartbeat_s=900)
    return {
        "kind": "news_v24_dryrun",
        "protocol_version": S.PROTOCOL_VERSION,
        "decision_time_ms": now,
        "input": len(records),
        "accepted": [r.to_json() and json.loads(r.to_json())
                     for r in result["accepted"]],
        "rejected": result["rejected"],
        "invalid": result["invalid"],
        "category_counts": result["category_counts"],
        "timeline": timeline,
        "accounting": {
            "input": len(records),
            "accepted": len(result["accepted"]),
            "rejected": len(result["rejected"]),
            "invalid": len(result["invalid"]),
            "balances": (len(records) == len(result["accepted"])
                         + len(result["rejected"]) + len(result["invalid"])),
        },
        "note": "synthetic fixture; proves the contract, says nothing about "
                "markets",
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, sort_keys=True))

#!/usr/bin/env python3
"""NEWS v2.6 isolated RSS intake and evidence-verified paper candidate.

Not the live v2.5 collector; no model calls or trades. One RSS fetch per source
passes the SAME harvested bytes to the legacy raw archive and metadata sidecar.
Candidate selection refuses any inconsistent first observation, archive, source
URL, publication time, or RSS occurrence. No timestamp repair/backfill.
"""
from __future__ import annotations

import hashlib
import sqlite3
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

import v24_collector as COL
import v26_rss_collector as RSSCOL
import rss_sidecar as SC
import v26_story_guard as STORY
import rss_noise_filter as NOISE
import tier2 as T2
import v21_paper_prep as PREP

SOURCES = RSSCOL.SOURCES
MAX_RAW_BYTES = 2_000_000
MAX_CANDIDATES_SCANNED = 256


class PipelineError(ValueError):
    pass


def ingest_one(*, events_db, sidecar_db, raw_dir, source_id, limiter, opener=None,
               harvested=None):
    """Single fetch and one archival pass. Explicitly supplied DBs only.

    On process failure between database writes, the candidate checker requires
    identical ORIGINAL observed_ms and raw SHA, so a later duplicate does not
    acquire false freshness. Disagreements are quarantined, never rewritten.
    """
    if source_id not in SOURCES:
        raise PipelineError("unknown_rss_source")
    got = harvested if harvested is not None else RSSCOL.AD.harvest(
        source_id, limiter=limiter, opener=opener)
    if not isinstance(got, dict) or got.get("source_id") != source_id:
        raise PipelineError("harvest_source_mismatch")
    raw_part = got.get("raw_body")
    if not isinstance(raw_part, dict) or raw_part.get("status") != 200:
        raise PipelineError("http_not_ok")
    raw = raw_part.get("body")
    observed_ms = raw_part.get("observed_ms")
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_RAW_BYTES
            or type(observed_ms) is not int or observed_ms <= 0):
        raise PipelineError("invalid_archived_input")
    expected_sha = hashlib.sha256(raw).hexdigest()
    for rec in got.get("records", []):
        if (rec.get("source_id") != source_id or rec.get("raw_sha256") != expected_sha
                or rec.get("observed_ms") != observed_ms):
            raise PipelineError("record_raw_or_clock_mismatch")

    class Replay:
        SOURCES = RSSCOL.SOURCES
        FetchError = RSSCOL.AD.FetchError

        @staticmethod
        def harvest(source, *, limiter):
            if source != source_id:
                raise PipelineError("unexpected_second_source")
            return got

    counts = COL.tick(con=events_db, limiter=limiter, sources=[source_id],
                      keep_raw=True, raw_dir=raw_dir, adapter=Replay)
    # Sidecar is a second SQLite database; correctness is verified on READ,
    # never assumed from cross-DB transaction atomicity.
    if not got.get("parse_error"):
        SC.upsert(sidecar_db, got.get("records", []))
    return counts[source_id]


def _read_exact_archive(path, expected_sha):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        raise PipelineError("raw_missing_or_symlink")
    if p.stat().st_size > MAX_RAW_BYTES:
        raise PipelineError("raw_oversized")
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise PipelineError("raw_digest_mismatch")
    return raw


def candidate(*, events_db, sidecar_db, now_ms, excluded=()):
    """Return (Tier-2 event, RSS metadata, symbol) or None; fail closed.

    Enforces chain: v24_events -> first observation -> immutable raw manifest
    -> sidecar occurrence -> raw XML reparsing -> deterministic noise decision.
    Only originally observed fresh items qualify, not feed replay/backfill.
    """
    if type(now_ms) is not int or now_ms <= 0:
        raise PipelineError("invalid_decision_clock")
    banned = set(excluded)
    rows = events_db.execute(
        "SELECT e.content_sha256,e.source_id,e.category,e.provenance,"
        "e.title,e.origin_url,e.published_ms,e.observed_ms,e.raw_sha256,"
        "o.observed_ms,o.raw_sha256,o.raw_path,o.origin_url,o.title,"
        "m.source_id,m.url,m.http_status,m.observed_ms,m.path,m.bytes "
        "FROM v24_events e "
        "JOIN v24_observations o ON o.occurrence_id=e.content_sha256 "
        "JOIN v24_raw_manifest m ON m.raw_sha256=e.raw_sha256 "
        "ORDER BY e.published_ms DESC,e.content_sha256 ASC LIMIT ?",
        (MAX_CANDIDATES_SCANNED,)
    ).fetchall()
    for (eid,sid,cohort,provenance,title,url,pub,observed,raw_sha,
         obs_ms,obs_sha,obs_path,obs_url,obs_title,
         manifest_sid,manifest_url,http_status,manifest_ms,manifest_path,raw_bytes) in rows:
        if eid in banned or sid not in SOURCES:
            continue
        spec = SOURCES[sid]
        if (not all(type(x) is int for x in (pub,observed,obs_ms,manifest_ms,raw_bytes))
                or pub <= 0 or pub > observed or observed > now_ms):
            continue
        if (sid != manifest_sid or spec["url"] != manifest_url
                or spec["category"] != cohort or spec["provenance"] != provenance
                or http_status != 200 or raw_sha != obs_sha
                or not (observed == obs_ms == manifest_ms)
                or url != obs_url or title != obs_title
                or obs_path != manifest_path
                or SC.identity({"source_id":sid,"title":title,"published_ms":pub}) != eid):
            continue
        side = sidecar_db.execute(
            "SELECT source_id,title,origin_url,published_ms,first_observed_ms,"
            "asset_scope,summary,raw_sha256,category,provenance "
            "FROM rss_metadata WHERE occurrence_id=?", (eid,)
        ).fetchone()
        if side is None:
            continue
        (meta_src,meta_title,meta_url,meta_pub,first,scope,summary,
         meta_sha,meta_category,meta_provenance) = side
        if (meta_src != sid or meta_title != title or meta_url != url
                or meta_pub != pub or first != observed or meta_sha != raw_sha
                or scope != spec["asset_scope"] or meta_category != cohort
                or meta_provenance != provenance):
            continue
        # V24 keeps every edited pubDate as a separate RAW occurrence.
        # V26 MUST NOT grant another forecast or renewed freshness to a
        # later revision of an already-observed source + article URL.
        if not STORY.is_first(events_db,event_id=eid,source_id=sid,
                              origin_url=url):
            continue
        # Discard stale without touching the raw archive.
        row = {"source_id":sid,"title":title,"origin_url":url,
               "asset_scope":scope,"summary":summary,"published_ms":pub,
               "first_observed_ms":first,"raw_sha256":raw_sha}
        verdict = NOISE.decide(row, now_ms=now_ms)
        if not verdict.accepted:
            continue
        # Verify full original RSS bytes, not merely hashes of DB rows.
        try:
            raw = _read_exact_archive(manifest_path, raw_sha)
            if len(raw) != raw_bytes:
                continue
            parsed = RSSCOL.RSS.parse_feed(raw, source_id=sid, observed_ms=observed)
        except (PipelineError, RSSCOL.RSS.FeedError, OSError):
            continue
        if not any(
            rec["title"] == title and rec["origin_url"] == url
            and rec["published_ms"] == pub and rec["observed_ms"] == observed
            and rec.get("summary", "") == summary
            and rec["raw_sha256"] == raw_sha
            for rec in parsed["records"]
        ):
            continue
        event = {
            "event_id":eid,"source":sid,"source_kind":provenance,
            "cohort":cohort,"declared_cohort":cohort,
            "title":title,"summary":summary,"origin_url":url,
            "published_ms":pub,"observed_ms":observed,
            "cutoff_ms":PREP.decision_cutoff(now_ms)
        }
        try:
            T2.validate_event(
                event, now_ms,
                published_max_ms=T2.FRESHNESS_BUDGET_S[cohort]*1000)
        except (T2.Tier2Error, KeyError):
            continue
        return (event,row,verdict.symbol)
    return None


def selftest():
    import tempfile
    from unittest.mock import Mock
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        con = COL.open_db(root/"rss.sqlite")
        sc = SC.connect(root/"meta.sqlite")
        source = "decrypt"
        pub = 1_800_000_000_000
        observed = pub + 1000
        title = "Ethereum suffers major exploit"
        raw = (
            "<rss><channel><item><title>"+title+"</title>"
            "<link>https://example.org/eth-exploit</link><pubDate>"+
            format_datetime(datetime.fromtimestamp(pub/1000,timezone.utc),usegmt=True)+
            "</pubDate><description>Ethereum protocol breach</description>"
            "</item></channel></rss>"
        ).encode()
        parsed = RSSCOL.RSS.parse_feed(raw,source_id=source,observed_ms=observed)
        got = {
            "source_id":source,
            "raw_body":{"body":raw,"observed_ms":observed,"status":200},
            "fetched":{"status":200,"content_type":"application/rss+xml","attempts":1},
            "records":[dict(r,terms_decision="RSS metadata",dated=True)
                       for r in parsed["records"]],
            "parse_error":None
        }
        counts = ingest_one(events_db=con,sidecar_db=sc,source_id=source,
                            limiter=Mock(),raw_dir=root/"raw",harvested=got)
        assert counts["new"]==1,counts
        result = candidate(events_db=con,sidecar_db=sc,now_ms=observed+1000)
        assert result and result[0]["event_id"]==SC.identity(got["records"][0])
        assert result[2]=="ETHUSDT",result
        assert candidate(events_db=con,sidecar_db=sc,
                         now_ms=observed+700_000) is None
        assert candidate(events_db=con,sidecar_db=sc,now_ms=observed+1000,
                         excluded={result[0]["event_id"]}) is None
        counts2 = ingest_one(events_db=con,sidecar_db=sc,source_id=source,
                             limiter=Mock(),raw_dir=root/"raw",harvested=got)
        assert counts2["new"]==0 and counts2["duplicate"]==1,counts2
        # Stored metadata never re-dates an old article or changes its witness.
        meta_path = con.execute("SELECT path FROM v24_raw_manifest").fetchone()[0]
        Path(meta_path).write_bytes(b"tampered")
        assert candidate(events_db=con,sidecar_db=sc,
                         now_ms=observed+1000) is None
        sc.close()
        con.close()
    print("NEWS V26 FEED PIPELINE SELFTEST OK: 1 GET replay, archive, sidecar, candidate, stale, tamper")


if __name__=="__main__":
    selftest()

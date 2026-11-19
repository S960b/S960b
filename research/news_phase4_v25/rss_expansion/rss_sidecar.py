#!/usr/bin/env python3
"""NEWS v2.6 sidecar: immutable RSS metadata per occurrence, isolated from v24.

No model calls, no series admissions. Use after RSS14Adapter.harvest, before
or after the legacy collector tick. Sidecar is an independent SQLite database.
Archive raw bytes in legacy collector; this stores metadata and provenance only.
"""
from __future__ import annotations
import hashlib
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS rss_metadata (
 occurrence_id TEXT PRIMARY KEY,
 source_id TEXT NOT NULL,
 title TEXT NOT NULL,
 origin_url TEXT NOT NULL,
 published_ms INTEGER NOT NULL,
 first_observed_ms INTEGER NOT NULL,
 last_observed_ms INTEGER NOT NULL,
 asset_scope TEXT NOT NULL,
 summary TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL,
 category TEXT NOT NULL,
 provenance TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rss_metadata_pub ON rss_metadata(published_ms);
CREATE INDEX IF NOT EXISTS rss_metadata_scope ON rss_metadata(asset_scope,published_ms);
"""
VALID_SCOPES = frozenset(("BTC", "ETH", "SOL", "GLOBAL", "MULTI"))
MAX_SUMMARY = 600

def identity(rec):
    # Exactly the same identity algorithm as v24_collector.occurrence_id.
    return hashlib.sha256(
        f"{rec['source_id']}|{rec['title']}|{rec['published_ms']}".encode()
    ).hexdigest()

def connect(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=5)
    con.executescript(DDL)
    return con

def upsert(con, records):
    """Atomic metadata ingestion. Reject malformed batches without partial writes.

    Repeated observations preserve original first_seen and first summary.
    The raw SHA is retained from the first observation, enabling raw witness
    lookup even when a publisher later changes the feed response.
    """
    checked = []
    for rec in records:
        source = rec["source_id"]
        title = rec["title"]
        url = rec["origin_url"]
        pub = rec["published_ms"]
        obs = rec["observed_ms"]
        scope = rec["asset_scope"]
        digest = rec["raw_sha256"]
        summary = rec.get("summary", "")
        if not (isinstance(source, str) and source and
                isinstance(title, str) and title and
                isinstance(url, str) and url.startswith("https://") and
                type(pub) is int and type(obs) is int and 0 < pub <= obs and
                scope in VALID_SCOPES and
                isinstance(digest, str) and len(digest) == 64 and
                all(c in "0123456789abcdef" for c in digest) and
                isinstance(summary, str) and len(summary) <= MAX_SUMMARY):
            raise ValueError("invalid_rss_metadata")
        checked.append((identity(rec), source, title, url, pub, obs, obs,
                        scope, summary, digest, rec["category"], rec["provenance"]))
    with con:
        before = con.total_changes
        con.executemany("""
          INSERT INTO rss_metadata
          (occurrence_id,source_id,title,origin_url,published_ms,
           first_observed_ms,last_observed_ms,asset_scope,summary,raw_sha256,
           category,provenance)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(occurrence_id) DO UPDATE SET
            last_observed_ms=MAX(rss_metadata.last_observed_ms,excluded.last_observed_ms)
        """, checked)
        changes = con.total_changes - before
    return {"seen": len(checked), "sqlite_changes": changes}

def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        con = connect(Path(d) / "meta.sqlite")
        r = {"source_id":"cryptoslate", "title":"Bitcoin ETF approved",
             "origin_url":"https://example.org/btc", "published_ms":1000,
             "observed_ms":2000, "asset_scope":"BTC",
             "summary":"Short description", "raw_sha256":"a"*64,
             "category":"crypto", "provenance":"secondary_media"}
        assert upsert(con,[r])["seen"] == 1
        r2 = dict(r, observed_ms=3000, summary="Changed", raw_sha256="b"*64)
        upsert(con,[r2])
        row = con.execute("SELECT first_observed_ms,last_observed_ms,summary,raw_sha256 FROM rss_metadata").fetchone()
        assert row == (2000,3000,"Short description","a"*64),row
        assert con.execute("SELECT COUNT(*) FROM rss_metadata").fetchone()[0] == 1
        bad = dict(r, published_ms=4000)
        try:
            upsert(con,[r,bad])
            raise AssertionError("accepted future date")
        except ValueError:
            pass
        assert con.execute("SELECT COUNT(*) FROM rss_metadata").fetchone()[0] == 1
        con.close()
    print("RSS SIDECAR SELFTEST OK")

if __name__ == "__main__":
    selftest()

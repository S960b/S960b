#!/usr/bin/env python3
"""NEWS v2.4 — bounded, allowlisted news collector into its own namespace.

Separate database, separate version. It never touches the v2.1 ledger, never
calls a model, and refuses to invent a timestamp: an item is stored with the
`published_ms` parsed from its own feed, and `observed_ms` taken when we fetched.
Freshness is computed from those two, so an old RSS item cannot look new.
"""
import hashlib
import json
import sqlite3
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import v24_adapters as AD

DB = Path.home() / ".local/share/news-phase4-v24/v24.sqlite"
RAW_DIR = Path.home() / ".local/share/news-phase4-v24/raw"

SCHEMA = """
CREATE TABLE IF NOT EXISTS v24_events(
 content_sha256 TEXT PRIMARY KEY,
 source_id TEXT NOT NULL,
 category TEXT NOT NULL,
 provenance TEXT NOT NULL,
 title TEXT NOT NULL,
 origin_url TEXT NOT NULL,
 published_ms INTEGER NOT NULL,
 observed_ms INTEGER NOT NULL,
 raw_sha256 TEXT NOT NULL,
 terms_decision TEXT,
 first_seen_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS v24_fetch_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 at_ms INTEGER NOT NULL, source_id TEXT NOT NULL, url TEXT NOT NULL,
 http_status INTEGER, content_type TEXT, attempts INTEGER,
 records_seen INTEGER, records_new INTEGER, error TEXT);
-- Undated official state (e.g. exchange system status) is kept apart from news.
-- It has no event time, so it can never enter an event query by accident.
-- Every distinct source occurrence, keyed by origin so two outlets publishing
-- the same headline at the same second do not collapse into one row.
CREATE TABLE IF NOT EXISTS v24_observations(
 occurrence_id TEXT PRIMARY KEY,     -- sha256(source_id | title | published)
 semantic_cluster_id TEXT NOT NULL,  -- sha256(title | published), no source
 source_id TEXT NOT NULL,
 title TEXT NOT NULL, origin_url TEXT NOT NULL,
 published_ms INTEGER NOT NULL, observed_ms INTEGER NOT NULL,
 raw_sha256 TEXT, raw_path TEXT,
 first_seen_ms INTEGER NOT NULL);
-- One row per archived response, so a stored event can point at the exact
-- bytes it came from. No headers, no credentials, no token values.
CREATE TABLE IF NOT EXISTS v24_raw_manifest(
 raw_sha256 TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL,
 http_status INTEGER, observed_ms INTEGER, bytes INTEGER,
 path TEXT NOT NULL, verified_ms INTEGER);
CREATE TABLE IF NOT EXISTS v24_state(
 raw_sha256 TEXT NOT NULL, source_id TEXT NOT NULL, body TEXT NOT NULL,
 observed_ms INTEGER NOT NULL, PRIMARY KEY(raw_sha256, observed_ms));
"""


def open_db(path=DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.executescript(MIGRATION_SCHEMA)   # alias/meta exist before any tick
    con.commit()
    return con


def parse_published(text):
    """RFC-822 date from RSS -> epoch ms. Unparseable -> None, never "now"."""
    try:
        return int(parsedate_to_datetime(text).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def occurrence_id(source_id, title, published_ms):
    """Per-origin identity: same story from two feeds is two occurrences."""
    return hashlib.sha256(
        f"{source_id}|{title}|{published_ms}".encode()).hexdigest()


def semantic_cluster_id(title, published_ms):
    """Cross-source cluster: same text at the same instant, source ignored."""
    return hashlib.sha256(f"{title}|{published_ms}".encode()).hexdigest()


def archive_raw(*, fetched, source_id, raw_dir=RAW_DIR):
    """Persist the exact response bytes, append-only, under their own digest.

    A digest without the bytes is not reconstructible: a reader could verify that
    nothing changed but could never re-parse the feed. Never overwrites, never
    stores headers that could carry credentials.
    """
    body = fetched.get("body")
    if body is None:
        return None
    if isinstance(body, str):
        body = body.encode()
    digest = hashlib.sha256(body).hexdigest()
    day = time.strftime("%Y%m%d", time.gmtime(fetched.get("observed_ms", time.time() * 1000) / 1000))
    out_dir = Path(raw_dir) / day
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = out_dir / f"{source_id}-{digest}.bin"
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        path.chmod(0o600)
    return {"raw_sha256": digest, "raw_path": str(path), "bytes": len(body)}


MIGRATION_SCHEMA = """
-- Cross-version identity. Historical rows were keyed sha(title|published) with
-- no source; the canonical identity is sha(source|title|published). An alias
-- keyed on the ORIGINAL key lets the store recognise a pre-migration row when
-- the new feed presents the same item under a new id, so a tick cannot
-- re-insert 70 historical events as 70 new ones.
CREATE TABLE IF NOT EXISTS v24_identity_alias(
 legacy_key TEXT PRIMARY KEY,        -- sha(title|published), the old key
 canonical_id TEXT NOT NULL,         -- sha(source|title|published)
 source_id TEXT, published_ms INTEGER, title TEXT,
 created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS v24_meta(
 key TEXT PRIMARY KEY, value TEXT NOT NULL, at_ms INTEGER NOT NULL);
"""

LEGACY_CLASS = "legacy_no_raw"


def ensure_migration_schema(con):
    con.executescript(MIGRATION_SCHEMA)
    con.commit()


def migration_state(con):
    ensure_migration_schema(con)      # a fresh DB has no meta table yet
    row = con.execute("SELECT value FROM v24_meta WHERE key='identity_migration'"
                      ).fetchone()
    return row[0] if row else "none"


def migrate_identity(con=None, *, path=None, dry_run=False):
    """Freeze canonical identity for historical rows WITHOUT rewriting them.

    The original `content_sha256` values stay exactly as written; a stable alias
    row is added so the live path can match a legacy row to a canonical id. Safe
    to run repeatedly: the alias table is the state.
    """
    if con is None:
        con = sqlite3.connect(str(path))
    ensure_migration_schema(con)
    rows = con.execute(
        "SELECT content_sha256, source_id, title, published_ms FROM v24_events"
    ).fetchall()
    added = 0
    now = int(time.time() * 1000)
    for old_id, src, title, pub in rows:
        legacy_key = semantic_cluster_id(title, pub)
        canonical = occurrence_id(src, title, pub)
        cur = con.execute(
            "INSERT OR IGNORE INTO v24_identity_alias(legacy_key, canonical_id,"
            " source_id, published_ms, title, created_ms) VALUES(?,?,?,?,?,?)",
            (legacy_key, canonical, src, pub, title, now))
        added += cur.rowcount
    if not dry_run:
        con.execute("INSERT OR REPLACE INTO v24_meta(key,value,at_ms) "
                    "VALUES('identity_migration',?,?)",
                    ("done:" + str(len(rows)), now))
        con.commit()
    return {"rows": len(rows), "aliases_added": added,
            "state": migration_state(con)}


def resolve_identity(con, *, source_id, title, published_ms):
    """Canonical id for an incoming item, honouring the legacy alias.

    Returns (id, is_legacy). A pre-migration row is matched through its alias and
    keeps its ORIGINAL stored key, so the tick is a no-op rather than a duplicate.
    """
    canonical = occurrence_id(source_id, title, published_ms)
    hit = con.execute("SELECT legacy_key, canonical_id FROM v24_identity_alias "
                      "WHERE canonical_id=? OR legacy_key=?",
                      (canonical, semantic_cluster_id(title, published_ms))
                      ).fetchone()
    if hit:
        existing = con.execute("SELECT content_sha256 FROM v24_events WHERE "
                               "content_sha256=?", (hit[1],)).fetchone()
        if existing:
            return hit[1], True
        legacy = con.execute("SELECT content_sha256 FROM v24_events WHERE "
                             "content_sha256=?",
                             (hit[0],)).fetchone()
        if legacy:
            return legacy[0], True
        return canonical, False
    return canonical, False


def tick(*, con, limiter, sources=None, keep_raw=True):
    """One bounded collection pass. Returns per-source counts."""
    out = {}
    for sid in (sources or list(AD.SOURCES)):
        try:
            got = AD.harvest(sid, limiter=limiter)
        except AD.FetchError as exc:
            con.execute("INSERT INTO v24_fetch_log(at_ms,source_id,url,error) "
                        "VALUES(?,?,?,?)",
                        (int(time.time() * 1000), sid, AD.SOURCES[sid]["url"],
                         str(exc)))
            con.commit()
            out[sid] = {"error": str(exc)}
            continue

        # One archive entry per successful HTTP response, even when the parsed
        # feed yields no dated items: the bytes are the provenance, the items are
        # a derivation. A zero-item poll with no archived body is unverifiable.
        archived_for_source = (archive_raw(fetched=got.get("raw_body") or {},
                                          source_id=sid)
                              if keep_raw else None)
        if archived_for_source:
            con.execute(
                "INSERT OR IGNORE INTO v24_raw_manifest(raw_sha256, source_id,"
                " url, http_status, observed_ms, bytes, path, verified_ms) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (archived_for_source["raw_sha256"], sid,
                 AD.SOURCES[sid]["url"], got["fetched"]["status"],
                 (got.get("raw_body") or {}).get("observed_ms"),
                 archived_for_source["bytes"],
                 archived_for_source["raw_path"],
                 int(time.time() * 1000)))
            con.commit()

        new = duplicated = unparseable = state_recorded = 0
        for rec in got["records"]:
            if rec.get("dated", True) is False:
                # A status endpoint is state, not an event. It is stored in its
                # own table with observed_ms and no published_ms, so it can
                # never be mistaken for a dated news item.
                con.execute("INSERT OR IGNORE INTO v24_state"
                            "(raw_sha256,source_id,body,observed_ms) "
                            "VALUES(?,?,?,?)",
                            (rec["raw_sha256"], rec["source_id"],
                             rec["title"], rec["observed_ms"]))
                state_recorded += 1
                continue
            published = parse_published(rec.get("published_raw"))
            if published is None:
                # No usable publication date: using fetch time as event time
                # would fabricate freshness, so the item is counted and logged.
                unparseable += 1
                continue
            # Two identities, deliberately different:
            #   occurrence_id  - this source, this item: the row we store
            #   cluster_id     - same text at the same instant across sources
            # Keying storage on the cluster suppressed a legitimate independent
            # publication, so the primary key is per-origin.
            #
            # The alias table bridges the pre-migration key, so a historical row
            # is recognised instead of being re-inserted as a new event.
            occ, is_legacy = resolve_identity(con, source_id=rec["source_id"],
                                              title=rec["title"],
                                              published_ms=published)
            cluster = semantic_cluster_id(rec["title"], published)
            archived = archived_for_source
            con.execute(
                "INSERT OR IGNORE INTO v24_observations(occurrence_id,"
                "semantic_cluster_id,source_id,title,origin_url,published_ms,"
                "observed_ms,raw_sha256,raw_path,first_seen_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (occ, cluster, rec["source_id"], rec["title"], rec["origin_url"],
                 published, rec["observed_ms"],
                 (archived or {}).get("raw_sha256"),
                 (archived or {}).get("raw_path"), int(time.time() * 1000)))
            digest = occ
            try:
                con.execute(
                    "INSERT INTO v24_events(content_sha256,source_id,category,"
                    "provenance,title,origin_url,published_ms,observed_ms,"
                    "raw_sha256,terms_decision,first_seen_ms) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (digest, rec["source_id"], rec["category"],
                     rec["provenance"], rec["title"], rec["origin_url"],
                     published, rec["observed_ms"], rec["raw_sha256"],
                     rec.get("terms_decision"),
                     int(time.time() * 1000)))
                new += 1
            except sqlite3.IntegrityError:
                duplicated += 1
        con.execute(
            "INSERT INTO v24_fetch_log(at_ms,source_id,url,http_status,"
            "content_type,attempts,records_seen,records_new) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (int(time.time() * 1000), sid, AD.SOURCES[sid]["url"],
             got["fetched"]["status"], got["fetched"]["content_type"],
             got["fetched"]["attempts"], len(got["records"]), new))
        con.commit()
        out[sid] = {"seen": len(got["records"]), "new": new,
                    "duplicate": duplicated, "unparseable": unparseable,
                    "state": state_recorded}
    return out


def freshness_report(con, *, now_ms=None):
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    rows = con.execute(
        "SELECT category, COUNT(*), MIN(published_ms), MAX(published_ms) "
        "FROM v24_events GROUP BY category ORDER BY category").fetchall()
    return [{"category": c, "count": n,
             "newest_age_s": int((now_ms - mx) / 1000),
             "oldest_age_s": int((now_ms - mn) / 1000)}
            for c, n, mn, mx in rows]


if __name__ == "__main__":
    lim = AD.RateLimiter()
    con = open_db()
    try:
        result = tick(con=con, limiter=lim)
        print(json.dumps({"tick": result,
                          "freshness": freshness_report(con),
                          "total": con.execute(
                              "select count(*) from v24_events").fetchone()[0]},
                         indent=1, sort_keys=True))
    finally:
        con.close()

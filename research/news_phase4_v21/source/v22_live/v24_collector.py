#!/usr/bin/env python3
"""NEWS v2.4 — bounded, allowlisted news collector into its own namespace.

Separate database, separate version. It never touches the v2.1 ledger, never
calls a model, and refuses to invent a timestamp: an item is stored with the
`published_ms` parsed from its own feed, and `observed_ms` taken when we fetched.
Freshness is computed from those two, so an old RSS item cannot look new.
"""
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
"""


def open_db(path=DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def parse_published(text):
    """RFC-822 date from RSS -> epoch ms. Unparseable -> None, never "now"."""
    try:
        return int(parsedate_to_datetime(text).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


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

        new = duplicated = unparseable = 0
        for rec in got["records"]:
            published = parse_published(rec.get("published_raw"))
            if published is None:
                # A status endpoint has no publication date. Treating the fetch
                # time as the event time would fabricate freshness, so the item
                # is counted as unparseable and logged, never stored as news.
                unparseable += 1
                continue
            digest = AD.hashlib.sha256(
                (rec["title"] + "|" + str(published)).encode()).hexdigest()
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
                    "duplicate": duplicated, "unparseable": unparseable}
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

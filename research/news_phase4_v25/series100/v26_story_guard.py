#!/usr/bin/env python3
"""NEWS v2.6 publication lineage: do not predict twice on RSS pubDate edits.

V24 remains a byte-for-byte RAW/occurrence archive. Its occurrence_id intentionally
includes publisher pubDate; that is NOT a stable identity for a forecast.
This module is read-only and is applied at candidate selection and at the
durable series claim. It does not revise the original observed publication time.
"""
from __future__ import annotations

import hashlib
import urllib.parse

# Only known attribution parameters are removed. Query parameters such as
# "id", "article", "story", "p" or other publisher article identifiers MUST
# remain part of the story identity to prevent false cross-article dedup.
TRACKERS=frozenset(("fbclid","gclid","gbraid","wbraid","mc_cid","mc_eid",
                   "igshid","ref_src","ref","ref_id","source",
                   "campaign","_ga","_gl","_hsenc","_hsmi"))

class StoryError(ValueError):
    pass


def canonical_url(url):
    if type(url) is not str:
        raise StoryError("invalid_story_url")
    try:
        parts=urllib.parse.urlsplit(url)
        if (parts.scheme.lower()!="https" or not parts.hostname
                or parts.username is not None or parts.password is not None):
            raise StoryError("invalid_story_url")
        port=parts.port
        host=parts.hostname.lower().rstrip(".")
        if not host or port not in (None,443):
            # Nonstandard ports remain part of the publication identity.
            host+=(":"+str(port)) if port is not None else ""
        if ":" in parts.hostname and not host.startswith("["):
            host="["+parts.hostname.lower()+"]"+(":"+str(port) if port else "")
        query=urllib.parse.parse_qsl(parts.query,keep_blank_values=True)
        retained=[(k,v) for k,v in query
                  if not k.lower().startswith("utm_") and k.lower() not in TRACKERS]
        return urllib.parse.urlunsplit(
            ("https",host,parts.path or "/",
             urllib.parse.urlencode(retained,doseq=True),""))
    except ValueError as exc:
        raise StoryError("invalid_story_url") from exc


def story_key(source_id, origin_url):
    if type(source_id) is not str or not source_id:
        raise StoryError("invalid_story_source")
    identity=source_id+"\x00"+canonical_url(origin_url)
    return hashlib.sha256(("v26-publication-v1\x00"+identity).encode()).hexdigest()


def is_first(events_db, *, event_id, source_id, origin_url):
    """Check against ALL archived versions, not a recent LIMIT 256 subset.

    Earliest REAL observed version wins. Newer pubDate can never make a
    previously observed publication a fresh/second prediction.
    """
    key=story_key(source_id,origin_url)
    first=None
    for eid,url,observed,first_seen in events_db.execute(
        "SELECT content_sha256,origin_url,observed_ms,first_seen_ms "
        "FROM v24_events WHERE source_id=?", (source_id,)):
        try:
            same=story_key(source_id,url)==key
        except StoryError:
            continue
        if same:
            # stable tie-breaker, avoids publisher time rewriting priority.
            marker=(int(observed),int(first_seen),eid)
            if first is None or marker<first[0]:
                first=(marker,eid)
    return first is not None and first[1]==event_id



SERIES_STORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS series_story_claims(
 story_key TEXT PRIMARY KEY,
 event_id TEXT NOT NULL UNIQUE REFERENCES series_claims(event_id)
);
CREATE TRIGGER IF NOT EXISTS v26_story_claim_immutable_update
BEFORE UPDATE ON series_story_claims
BEGIN SELECT RAISE(ABORT,'v26_story_claim_immutable'); END;
CREATE TRIGGER IF NOT EXISTS v26_story_claim_immutable_delete
BEFORE DELETE ON series_story_claims
BEGIN SELECT RAISE(ABORT,'v26_story_claim_immutable'); END;
"""


def install_series_schema(con):
    con.executescript(SERIES_STORY_SCHEMA)


def claim(con, *, event_id, semantic_cluster_id, story, trial_id, at_ms):
    """Single transaction protects BOTH cross-source cluster and publication.

    Legacy semantic cluster dedups matching headlines across outlets. The
    v2.6 stable story key separately prevents a changed pubDate/edited title
    for the SAME article URL from generating a second independent prediction.
    """
    import sqlite3
    import series_guard as G
    ids=(event_id,semantic_cluster_id,story,trial_id)
    if any(type(x) is not str or G.ID_RE.fullmatch(x) is None for x in ids):
        raise G.Refused("invalid_v26_story_claim_identity")
    con.execute("BEGIN IMMEDIATE")
    try:
        counts=G.counters(con)
        if counts["sealed"]>=G.SERIES_LIMIT or counts["calls_reserved"]>=G.CALL_LIMIT:
            raise G.Refused("series_budget_exhausted")
        con.execute(
            "INSERT INTO series_claims"
            "(event_id,semantic_cluster_id,trial_id,claimed_ms,state)"
            "VALUES(?,?,?,?,'claimed')",
            (event_id,semantic_cluster_id,trial_id,int(at_ms)))
        con.execute(
            "INSERT INTO series_story_claims(story_key,event_id) VALUES(?,?)",
            (story,event_id))
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise G.Refused("duplicate_v26_story_or_cross_source_cluster") from exc
    except BaseException:
        con.rollback()
        raise
    return trial_id


def selftest():
    import sqlite3
    a=story_key("decrypt","https://EXAMPLE.org/bitcoin?article=42&utm_source=rss")
    b=story_key("decrypt","https://example.org/bitcoin?article=42#top")
    assert a==b
    assert story_key("decrypt","https://example.org/bitcoin?article=43")!=a
    assert a==story_key("decrypt","https://example.org/bitcoin?article=42"
                       "&ref=rss&campaign=autumn&_ga=tracking")
    assert a==story_key("decrypt","https://example.org/bitcoin?article=42"
                       "&source=feed&ref_id=promo")
    assert story_key("decrypt","https://example.org/article?id=123") != (
           story_key("decrypt","https://example.org/article?id=124"))
    assert story_key("coindesk","https://example.org/bitcoin?article=42")!=a
    con=sqlite3.connect(":memory:")
    con.execute("CREATE TABLE v24_events(content_sha256 TEXT,source_id TEXT,"
                "origin_url TEXT,observed_ms INTEGER,first_seen_ms INTEGER)")
    con.executemany("INSERT INTO v24_events VALUES (?,?,?,?,?)",[
       ("old","decrypt","https://example.org/bitcoin?article=42",1000,1001),
       ("revision","decrypt","https://example.org/bitcoin?article=42"
        "&utm_campaign=updated",2000,2001),
       ("new","decrypt","https://example.org/bitcoin?article=43",2000,2001),
    ])
    assert is_first(con,event_id="old",source_id="decrypt",
                    origin_url="https://example.org/bitcoin?article=42")
    assert not is_first(con,event_id="revision",source_id="decrypt",
                        origin_url="https://example.org/bitcoin?article=42")
    assert is_first(con,event_id="new",source_id="decrypt",
                    origin_url="https://example.org/bitcoin?article=43")
    con.close()
    import tempfile
    import series_guard as G
    with tempfile.TemporaryDirectory() as temp:
        db=G.open_db(__import__("pathlib").Path(temp)/"series.sqlite")
        install_series_schema(db)
        claim(db,event_id="a"*64,semantic_cluster_id="b"*64,
              story="c"*64,trial_id="evt1",at_ms=100)
        for same_cluster,same_story in (("d"*64,"c"*64),
                                        ("b"*64,"e"*64)):
            try:
                claim(db,event_id="f"*64,semantic_cluster_id=same_cluster,
                      story=same_story,trial_id="evt2",at_ms=110)
                raise AssertionError("duplicate story or cluster accepted")
            except G.Refused:
                pass
        assert G.counters(db)["claimed"]==1
        db.close()
    print("NEWS V26 STORY GUARD SELFTEST OK: stable URL, publisher revisions, two unique claim keys")


if __name__=="__main__":
    selftest()

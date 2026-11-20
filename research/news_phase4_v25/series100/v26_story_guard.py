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

TRACKERS=frozenset(("fbclid","gclid","gbraid","wbraid","mc_cid","mc_eid",
                   "igshid","ref_src"))

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


def selftest():
    import sqlite3
    a=story_key("decrypt","https://EXAMPLE.org/bitcoin?article=42&utm_source=rss")
    b=story_key("decrypt","https://example.org/bitcoin?article=42#top")
    assert a==b
    assert story_key("decrypt","https://example.org/bitcoin?article=43")!=a
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
    print("NEWS V26 STORY GUARD SELFTEST OK: pubDate revision, URL tracking, distinct link")


if __name__=="__main__":
    selftest()

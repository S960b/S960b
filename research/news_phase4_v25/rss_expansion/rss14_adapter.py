#!/usr/bin/env python3
"""NEWS v2.6: 14-source RSS-only roster and normalized metadata extractor.

This is an ADDITIVE, unarmed component. No DB mutation, no model calls.
v24 immutable release and running collector must NOT be replaced until
integration tests prove exact raw witness / timestamp compatibility.

Raw XML is the audit record. HTML summaries are converted to a bounded text
field, NOT fetched from article pages. Future-dated items remain in the raw
feed but are excluded from the candidate list, never re-dated to 'now'.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser

# (URL, existing v2.5 category, provenance, intended market scope)
RSS14 = {
    "fed_press_monetary": ("https://www.federalreserve.gov/feeds/press_monetary.xml", "monetary_policy", "primary", "GLOBAL"),
    "sec_press": ("https://www.sec.gov/news/pressreleases.rss", "regulatory", "primary", "GLOBAL"),
    "whitehouse_actions": ("https://www.whitehouse.gov/presidential-actions/feed/", "geopolitics", "primary", "GLOBAL"),
    "bitcoin_core": ("https://bitcoincore.org/en/announcements.xml", "crypto", "primary", "BTC"),
    "ethereum_foundation": ("https://blog.ethereum.org/en/feed.xml", "crypto", "primary", "ETH"),
    "solana_status": ("https://status.solana.com/history.rss", "crypto", "primary", "SOL"),
    "coinbase_exchange_status": ("https://status.exchange.coinbase.com/history.rss", "crypto", "primary", "GLOBAL"),
    "kraken_incidents": ("https://status.kraken.com/history.rss", "crypto", "primary", "GLOBAL"),
    "circle_status": ("https://status.circle.com/history.rss", "crypto", "primary", "GLOBAL"),
    "coindesk": ("https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto", "secondary_media", "MULTI"),
    "cointelegraph": ("https://cointelegraph.com/rss", "crypto", "secondary_media", "MULTI"),
    "theblock": ("https://www.theblock.co/rss.xml", "crypto", "secondary_media", "MULTI"),
    "decrypt": ("https://decrypt.co/feed", "crypto", "secondary_media", "MULTI"),
    "cryptoslate": ("https://cryptoslate.com/feed/", "crypto", "secondary_media", "MULTI"),
}
SCHEMA = "news_v26_rss14_metadata_v1"
MAX_FEED_BYTES = 2_000_000
MAX_TITLE = 400
MAX_SUMMARY = 600
MAX_ITEMS = 1000

class FeedError(ValueError):
    pass

class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.omit = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.omit += 1
    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self.omit:
            self.omit -= 1
    def handle_data(self, data):
        if not self.omit:
            self.parts.append(data)

def plaintext(s, limit):
    if not s:
        return ""
    parser = _PlainText()
    parser.feed(s)
    parser.close()
    return " ".join(html.unescape(" ".join(parser.parts)).split())[:limit]

def local_name(tag):
    return str(tag).rsplit("}", 1)[-1].lower()

def first_value(item, names):
    wanted = set(names)
    for child in list(item):
        if local_name(child.tag) in wanted:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return None

def link_value(item):
    for child in list(item):
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href and child.attrib.get("rel", "alternate") == "alternate":
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    guid = first_value(item, ("guid",))
    return guid if guid and guid.startswith("https://") else None

def publication(raw):
    if not raw:
        return None
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            return None
    if dt.tzinfo is None:  # never invent source timezone
        return None
    try:
        ms = int(dt.timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None
    if ms <= 0:
        return None
    # Canonical GMT makes RSS and Atom compatible with legacy RFC822 parser.
    canonical = email.utils.format_datetime(dt.astimezone(timezone.utc), usegmt=True)
    return ms, canonical

def parse_feed(raw, *, source_id, observed_ms):
    if source_id not in RSS14:
        raise FeedError("unknown_source")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FEED_BYTES:
        raise FeedError("feed_size_or_type")
    if not isinstance(observed_ms, int) or isinstance(observed_ms, bool) or observed_ms <= 0:
        raise FeedError("invalid_observed_ms")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise FeedError("xml_entity_or_doctype_refused")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedError("invalid_xml") from exc
    root_kind = local_name(root.tag)
    if root_kind not in ("rss", "rdf", "feed"):
        raise FeedError("not_rss_or_atom")
    item_type = "entry" if root_kind == "feed" else "item"
    entries = [x for x in root.iter() if local_name(x.tag) == item_type]
    if len(entries) > MAX_ITEMS:
        raise FeedError("too_many_entries")
    source_url, category, provenance, scope = RSS14[source_id]
    digest = hashlib.sha256(raw).hexdigest()
    out = []
    failures = {"future_date": 0, "undated": 0, "bad_link_or_title": 0}
    for item in entries:
        title = plaintext(first_value(item, ("title",)), MAX_TITLE)
        link = link_value(item)
        date_raw = first_value(item, ("pubdate", "published", "issued", "date", "updated"))
        date = publication(date_raw)
        if not date:
            failures["undated"] += 1
            continue
        pub_ms, canonical = date
        if pub_ms > observed_ms:
            # Scheduled maintenance often has a future pubDate. Archive raw
            # unchanged but never let this masquerade as an already-seen event.
            failures["future_date"] += 1
            continue
        if not title or not link or urllib.parse.urlsplit(link).scheme != "https":
            failures["bad_link_or_title"] += 1
            continue
        # Parse item summaries as descriptive metadata, no article scraping.
        summary_raw = first_value(item, ("description", "summary", "encoded", "content"))
        summary = plaintext(summary_raw, MAX_SUMMARY)
        out.append({
            "source_id": source_id,
            "category": category,
            "provenance": provenance,
            "asset_scope": scope,
            "title": title,
            "summary": summary,
            "origin_url": link,
            "published_raw": canonical,
            "published_ms": pub_ms,
            "observed_ms": observed_ms,
            "raw_sha256": digest,
            "raw_feed_url": source_url,
            "metadata_only": True,
        })
    return {"schema": SCHEMA, "source_id": source_id, "raw_sha256": digest,
            "format": "atom" if root_kind == "feed" else "rss",
            "entries_seen": len(entries), "records": out, "excluded": failures}

def validate_roster():
    if len(RSS14) != 14:
        raise FeedError("expected_14_sources")
    for source_id, (url, category, provenance, scope) in RSS14.items():
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or not p.hostname:
            raise FeedError("non_https_feed:" + source_id)
        if category not in ("crypto", "monetary_policy", "regulatory", "geopolitics"):
            raise FeedError("unsupported_cohort:" + source_id)
        if scope not in ("BTC", "ETH", "SOL", "GLOBAL", "MULTI"):
            raise FeedError("unsupported_scope:" + source_id)
        if provenance not in ("primary", "secondary_media"):
            raise FeedError("unsupported_provenance:" + source_id)
    return True

def selftest():
    validate_roster()
    time_observed = 1795118400000
    rss = (b'<rss><channel><item><title>Bitcoin upgrade</title>'
           b'<link>https://example.org/bitcoin</link>'
           b'<pubDate>Thu, 19 Nov 2026 19:00:00 GMT</pubDate>'
           b'<description>&lt;b&gt;Upgrade&lt;/b&gt; details</description>'
           b'</item></channel></rss>')
    got = parse_feed(rss, source_id="bitcoin_core", observed_ms=time_observed)
    assert got["entries_seen"] == 1 and len(got["records"]) == 1, got
    assert got["records"][0]["summary"] == "Upgrade details"
    assert publication(got["records"][0]["published_raw"])[0] == got["records"][0]["published_ms"]
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom">'
            b'<entry><title>SOL network restored</title>'
            b'<link href="https://example.org/sol" rel="alternate"/>'
            b'<published>2026-11-19T19:00:00Z</published>'
            b'<summary>Incident restored</summary></entry></feed>')
    got = parse_feed(atom, source_id="solana_status", observed_ms=time_observed)
    assert len(got["records"]) == 1 and got["format"] == "atom", got
    future = parse_feed(atom, source_id="solana_status", observed_ms=1795111200000)
    assert future["records"] == [] and future["excluded"]["future_date"] == 1
    try:
        parse_feed(b'<!DOCTYPE foo><rss/>', source_id="solana_status", observed_ms=time_observed)
        assert False
    except FeedError:
        pass
    print("RSS14 ADAPTER SELFTEST OK: roster, RSS, Atom, RFC822, future-date, XML guard")

if __name__ == "__main__":
    selftest()

#!/usr/bin/env python3
"""NEWS v2.6 RSS-only candidate audit. No DB writes, no model calls, no API keys.

Probes exactly 14 approved candidates. Explicit source metadata, freshness,
RSS/Atom schema and permission/availability diagnostics are retained in JSON.
DO NOT convert probe success into permission for automated AI processing.
"""
from __future__ import annotations

import argparse
import email.utils
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SOURCES = (
    ("fed_press_monetary", "Federal Reserve", "official", "GLOBAL",
     "https://www.federalreserve.gov/feeds/press_monetary.xml", "rss"),
    ("sec_press", "SEC", "official", "GLOBAL",
     "https://www.sec.gov/news/pressreleases.rss", "rss"),
    ("whitehouse_actions", "White House", "official", "GLOBAL",
     "https://www.whitehouse.gov/presidential-actions/feed/", "rss"),
    ("bitcoin_core", "Bitcoin Core", "official", "BTC",
     "https://bitcoincore.org/en/rss/", "discover"),
    ("ethereum_foundation", "Ethereum Foundation", "official", "ETH",
     "https://blog.ethereum.org/en/feed.xml", "rss"),
    ("solana_status", "Solana Status", "official", "SOL",
     "https://status.solana.com/history.rss", "rss"),
    ("coinbase_exchange_status", "Coinbase Exchange Status", "official", "GLOBAL",
     "https://status.exchange.coinbase.com/history.rss", "rss"),
    ("kraken_incidents", "Kraken Status", "official", "GLOBAL",
     "https://status.kraken.com/history.rss", "rss"),
    ("circle_status", "Circle / USDC", "official", "GLOBAL",
     "https://status.circle.com/history.rss", "rss"),
    ("coindesk", "CoinDesk", "media", "MULTI",
     "https://www.coindesk.com/arc/outboundfeeds/rss/", "rss"),
    ("cointelegraph", "Cointelegraph", "media", "MULTI",
     "https://cointelegraph.com/rss", "rss"),
    ("theblock", "The Block", "media", "MULTI",
     "https://www.theblock.co/rss.xml", "rss"),
    ("decrypt", "Decrypt", "media", "MULTI",
     "https://decrypt.co/feed", "rss"),
    ("cryptoslate", "CryptoSlate", "media", "MULTI",
     "https://cryptoslate.com/feed/", "rss"),
)
MAX_BYTES = 1_500_000
USER_AGENT = "NEWS-RSS-Readiness/1.0 (+read-only feed format audit; contact: local operator)"
BLOCKED_FOR_MODEL = frozenset(("theblock",))  # Terms require review, not just HTTP 200.

def local(tag):
    return tag.rsplit("}", 1)[-1].lower()

def dated(s):
    if not s:
        return None
    value = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        return None  # Never fabricate a timezone.
    return int(d.timestamp() * 1000)

def parse_feed(raw, *, now_ms):
    if b"<!DOCTYPE" in raw[:512].upper() or b"<!ENTITY" in raw[:512].upper():
        raise ValueError("xml_doctype_or_entity_refused")
    root = ET.fromstring(raw)
    root_name = local(root.tag)
    if root_name not in ("rss", "rdf", "feed"):
        raise ValueError("not_rss_or_atom:" + root_name)
    item_name = "entry" if root_name == "feed" else "item"
    items = [node for node in root.iter() if local(node.tag) == item_name]
    times = []
    without_date = 0
    titles = 0
    summaries = 0
    links = 0
    future = 0
    samples = []
    for item in items:
        fields = {}
        for child in list(item):
            key = local(child.tag)
            if key not in fields or not fields[key]:
                fields[key] = "".join(child.itertext()).strip()
            if key == "link" and child.get("href"):
                fields["link"] = child.attrib["href"]
        if fields.get("title"):
            titles += 1
        if fields.get("description") or fields.get("summary") or fields.get("content") or fields.get("encoded"):
            summaries += 1
        if fields.get("link"):
            links += 1
        published = next((dated(fields.get(key)) for key in
                          ("pubdate", "published", "issued", "date", "updated")
                          if dated(fields.get(key)) is not None), None)
        if published is None:
            without_date += 1
        else:
            times.append(published)
            if published > now_ms + 600_000:
                future += 1
        if len(samples) < 3:
            samples.append({
                "title": fields.get("title", "")[:160],
                "dated": published is not None,
                "published_utc": (datetime.fromtimestamp(published/1000, tz=timezone.utc)
                                  .isoformat() if published else None)
            })
    if not items:
        raise ValueError("valid_xml_but_no_entries")
    latest = max(times) if times else None
    latest_age_s = int((now_ms - latest)/1000) if latest is not None else None
    return {
        "format": "atom" if root_name == "feed" else "rss",
        "entries": len(items),
        "dated_entries": len(times),
        "missing_dates": without_date,
        "future_dates_over_10min": future,
        "with_titles": titles,
        "with_summaries": summaries,
        "with_links": links,
        "newest_published_utc": (
            datetime.fromtimestamp(latest/1000, tz=timezone.utc).isoformat() if latest else None),
        "newest_age_seconds": latest_age_s,
        "samples": samples
    }

class Discovery(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.active = None
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag.lower() == "a" and a.get("href"):
            self.active = [a["href"], a.get("title", ""), a.get("type", "")]
        if tag.lower() == "link" and a.get("href"):
            self.links.append((a["href"], a.get("title", ""), a.get("type", "")))
    def handle_data(self, data):
        if self.active is not None:
            self.active[1] += " " + data
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.active is not None:
            self.links.append(tuple(self.active))
            self.active = None

def discovery_urls(page_url, raw):
    html = Discovery()
    html.feed(raw.decode("utf-8", "replace"))
    found = []
    for href, title, mime in html.links:
        candidate = urllib.parse.urljoin(page_url, href)
        parts = urllib.parse.urlsplit(candidate)
        # Only own-site candidates, never arbitrary links scraped from markup.
        if parts.scheme != "https" or parts.hostname != "bitcoincore.org":
            continue
        evidence = (href + " " + title + " " + mime).lower()
        if not any(x in evidence for x in ("rss", "xml", "feed", "announcement", "security", "release", "blog")):
            continue
        priority = (0 if "announc" in evidence or "security" in evidence
                    else 1 if "release" in evidence else 2)
        found.append((priority, candidate))
    return [url for _, url in sorted(set(found))[:6]]

def request(url, timeout_s):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise ValueError("https_required")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.5"
    })
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resolved = resp.geturl()
        if urllib.parse.urlsplit(resolved).scheme != "https":
            raise ValueError("insecure_redirect")
        raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("response_too_large")
        return {
            "status": getattr(resp, "status", None),
            "resolved_url": resolved,
            "content_type": resp.headers.get("Content-Type", ""),
            "bytes": len(raw),
        }, raw

def audit_source(source, *, timeout_s=12, now_ms=None):
    sid, name, kind, asset, url, mode = source
    now_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
    record = {
        "id": sid, "name": name, "kind": kind, "asset_scope": asset,
        "url": url, "mode": mode,
        "candidate_only": True,
        "model_use_permission": "review_required" if sid in BLOCKED_FOR_MODEL else "not_audited",
    }
    start = time.monotonic()
    try:
        meta, raw = request(url, timeout_s)
        record.update(meta)
        if mode == "discover":
            candidates = discovery_urls(meta["resolved_url"], raw)
            record["discovery_candidates"] = candidates
            if not candidates:
                raise ValueError("no_direct_bitcoin_core_feeds_found")
            errors = []
            for candidate in candidates:
                try:
                    submeta, subraw = request(candidate, timeout_s)
                    parsed = parse_feed(subraw, now_ms=now_ms)
                    record.update(submeta)
                    record.update(parsed)
                    record["status"] = "ok"
                    break
                except (Exception) as e:
                    errors.append(type(e).__name__ + ":" + str(e)[:120])
            else:
                raise ValueError("discovered_feeds_failed:" + ";".join(errors[:3]))
        else:
            record.update(parse_feed(raw, now_ms=now_ms))
            record["status"] = "ok"
        if record["dated_entries"] == 0:
            record["status"] = "no_dated_items"
        elif record["future_dates_over_10min"]:
            record["warnings"] = ["future_publication_timestamps"]
        if record["status"] == "ok" and record["newest_age_seconds"] is not None:
            # Not a hard failure: status/official feeds may be legitimately quiet.
            if record["newest_age_seconds"] > 7 * 86400:
                record.setdefault("warnings", []).append("quiet_for_over_7_days")
    except urllib.error.HTTPError as e:
        record.update({"status": "http_error", "http_status": e.code,
                       "error": "HTTP " + str(e.code)})
    except Exception as e:
        record.update({"status": "error", "error": type(e).__name__ + ":" + str(e)[:200]})
    record["elapsed_ms"] = round(1000 * (time.monotonic() - start))
    return record

def selftest():
    now = 1795000000000
    rss = (b'<rss version="2.0"><channel><item><title>BTC</title>'
           b'<pubDate>Thu, 19 Nov 2026 12:00:00 GMT</pubDate>'
           b'<link>https://example.org/a</link></item></channel></rss>')
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            b'<title>ETH</title><published>2026-11-19T12:00:00Z</published>'
            b'<link href="https://example.org/b"/></entry></feed>')
    assert parse_feed(rss, now_ms=now)["dated_entries"] == 1
    assert parse_feed(atom, now_ms=now)["with_links"] == 1
    assert dated("bad") is None
    assert dated("2026-11-19T12:00:00Z") is not None
    assert len(SOURCES) == len(set(x[0] for x in SOURCES)) == 14
    assert len(discovery_urls("https://bitcoincore.org/en/rss/",
        b'<a href="/en/security.xml">Security announcements</a>')) == 1
    try:
        parse_feed(b"<html/>", now_ms=now)
        raise AssertionError("HTML accepted as RSS")
    except ValueError:
        pass
    print("SELFTEST PASS: RSS, Atom, date, unique 14, feed discovery, HTML refusal")

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", help="Atomic JSON output path; default stdout")
    ap.add_argument("--timeout", type=float, default=12)
    ap.add_argument("--pause", type=float, default=0.4,
                    help="seconds between sources; avoid rapid repeated requests")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if not 2 <= args.timeout <= 30 or not 0.2 <= args.pause <= 10:
        ap.error("timeout/pause out of bounded range")
    report = {"schema": "news_v26_rss_probe_v1", "purpose": "read_only_preflight",
              "generated_utc": datetime.now(timezone.utc).isoformat(),
              "model_calls": 0, "sources": []}
    for i, source in enumerate(SOURCES):
        r = audit_source(source, timeout_s=args.timeout)
        report["sources"].append(r)
        print("%s: %s entries=%s dates=%s HTTP=%s" %
              (r["id"], r["status"], r.get("entries", "?"),
               r.get("dated_entries", "?"), r.get("http_status", r.get("status", "?"))),
              file=sys.stderr, flush=True)
        if i + 1 < len(SOURCES):
            time.sleep(args.pause)
    report["summary"] = {
        "candidate_count": len(SOURCES),
        "feeds_ok": sum(1 for v in report["sources"] if v["status"] == "ok"),
        "dated_feeds": sum(1 for v in report["sources"] if v.get("dated_entries", 0)),
        "model_permission_confirmed": 0,
        "model_calls": 0,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        path = Path(args.out)
        if path.is_symlink():
            raise RuntimeError("refuse_symlink_output")
        tmp = path.with_name(path.name + ".new")
        with tmp.open("x", encoding="utf-8") as out:
            out.write(encoded)
        tmp.replace(path)
    else:
        print(encoded)
    # Probe is observational: individual failed sources do not crash the audit.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

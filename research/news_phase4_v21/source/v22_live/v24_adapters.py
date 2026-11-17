#!/usr/bin/env python3
"""NEWS v2.4 — allowlisted live source adapters (PUBLIC, READ-ONLY, rate-limited).

Only endpoints that were verified reachable during this session are here, and
each carries the terms evidence that was actually observed. Nothing is scraped,
no paywall is touched, and a 403 is treated as a blocker to report — never as
something to work around.

Verified 2026-11-17 (HTTP code observed from this host, one request each):

  federalreserve.gov/feeds/press_monetary.xml   200 text/xml   RSS
  sec.gov/news/pressreleases.rss               200 rss+xml    RSS
  api.bls.gov/publicAPI/v2/timeseries/data     200 json       POST JSON
  api.kraken.com/0/public/SystemStatus         200 json       public status
  whitehouse.gov/presidential-actions/feed/    200 xml        RSS

Blocked / not used:
  api.coinbase.com/v2/status                   404  — endpoint gone
  bls.gov/developers/ and sec.gov/about/...    403  — QUARANTINED, see below
  Data.MarketWatch / third-party news           not attempted

TERMS NOTES (evidence, not licences):
  * The Fed's own feeds index page (200) documents RSS subscription — that is
    the basis for using the feed; it is not a blanket redistribution licence.
  * The BLS *API* answered 200 for an unauthenticated v2 request, which is the
    documented public behaviour; the BLS *web pages* answered 403 to this
    client. The 403 is recorded as a blocker for page scraping, not bypassed.
  * SEC answered 403 for several page requests within a minute, which is their
    fair-access threshold. SEC requests are therefore limited to ONE every two
    seconds, far below the 10 req/s ceiling.
"""
import hashlib
import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "MultiTool-NEWS-research/2.4 (offline research; contact S960b)"

# Hard limits, chosen well below any published ceiling.
MIN_INTERVAL_MS = {"sec.gov": 2_000, "default": 1_000}
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 20
MAX_ATTEMPTS = 2          # one retry only, never a retry storm


class FetchError(ValueError):
    pass


def _host(url):
    return url.split("://", 1)[-1].split("/", 1)[0].lower()


class RateLimiter:
    """Per-host minimum interval, held in memory for this process only."""

    def __init__(self, now=None):
        self._last = {}
        self._now = now or (lambda: int(time.time() * 1000))

    def wait_ms(self, url):
        host = _host(url)
        interval = MIN_INTERVAL_MS.get(".".join(host.split(".")[-2:]),
                                       MIN_INTERVAL_MS["default"])
        last = self._last.get(host)
        now = self._now()
        if last is None:
            self._last[host] = now
            return 0
        wait = max(0, interval - (now - last))
        self._last[host] = now + wait
        return wait


def fetch(url, *, limiter, opener=None, accept=None):
    """One bounded fetch. Returns raw bytes + observed time; never retries silently."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise FetchError("bad_url")
    opened = None
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        wait = limiter.wait_ms(url)
        if wait:
            time.sleep(wait / 1000.0)
        headers = {"User-Agent": USER_AGENT, "Accept": accept or "*/*"}
        req = urllib.request.Request(url, headers=headers)
        try:
            opened = (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S)
            raw = opened.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError("payload_too_large")
            return {"url": url, "body": raw,
                    "observed_ms": int(time.time() * 1000),
                    "status": getattr(opened, "status", 200),
                    "content_type": (opened.headers.get("Content-Type") or ""),
                    "attempts": attempts}
        except urllib.error.HTTPError as exc:
            # A 403 is a policy answer. Report it; never retry around it.
            raise FetchError("http_" + str(exc.code)) from None
        except urllib.error.URLError as exc:
            if attempts >= MAX_ATTEMPTS:
                raise FetchError("transport:" + type(exc.reason).__name__) from None
    raise FetchError("fetch_failed")


def _text(node, tag):
    found = node.find(tag)
    return (found.text or "").strip() if found is not None else None


def parse_rss(body, *, source_id, category, provenance, fetched):
    """RSS/Atom -> normalised records. Bad items are skipped, never invented."""
    root = ET.fromstring(body)
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")
    out = []
    for item in items:
        title = _text(item, "title")
        link = _text(item, "link") or _text(item, "guid")
        published = (_text(item, "pubDate") or _text(item, "updated")
                     or _text(item, "published"))
        if not title or not link or not published:
            continue
        out.append({"source_id": source_id, "category": category,
                    "provenance": provenance, "title": " ".join(title.split()),
                    "origin_url": link.strip(), "published_raw": published,
                    "observed_ms": fetched["observed_ms"],
                    "raw_sha256": hashlib.sha256(body).hexdigest()})
    return out


SOURCES = {
    "fed_press_monetary": {
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "category": "monetary_policy", "provenance": "primary",
        "format": "rss",
        "terms": "Fed feeds index documents RSS subscription (page returned 200)",
    },
    "sec_press": {
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "category": "regulatory", "provenance": "primary", "format": "rss",
        "terms": "SEC fair-access: <=10 req/s; we use 1 per 2 s",
    },
    "whitehouse_actions": {
        "url": "https://www.whitehouse.gov/presidential-actions/feed/",
        "category": "geopolitics", "provenance": "primary", "format": "rss",
        "terms": "public RSS feed; no stated redistribution licence — metadata only",
    },
    "kraken_status": {
        "url": "https://api.kraken.com/0/public/SystemStatus",
        "category": "crypto", "provenance": "official_secondary",
        "format": "json",
        # A status endpoint returns state, not an event: it carries no
        # publication date at all. Marking it dated=False keeps the collector
        # from promoting fetch time into event time.
        "dated": False,
        "terms": "public status endpoint, no key required",
    },
}

QUARANTINED = {
    "coinbase_status": "api.coinbase.com/v2/status returned 404 on 2026-11-17",
    "bls_web_pages": "bls.gov/developers/ returned 403 to this client",
    "sec_web_pages": "sec.gov/about/... returned 403 (fair-access threshold)",
    "bls_api": "reachable (200) but every BLS release is a scheduled statistic, "
               "not a news event; not wired until a category rule exists",
}


def harvest(source_id, *, limiter, opener=None):
    spec = SOURCES[source_id]
    fetched = fetch(spec["url"], limiter=limiter, opener=opener)
    # A successfully fetched response is evidence EVEN IF its XML/JSON is
    # malformed. Do not let one bad feed abort collection from other sources.
    # The caller archives raw bytes first, then records the per-source failure.
    parse_error = None
    if spec["format"] == "rss":
        try:
            records = parse_rss(fetched["body"], source_id=source_id,
                                category=spec["category"],
                                provenance=spec["provenance"], fetched=fetched)
        except ET.ParseError:
            records = []
            parse_error = "rss_malformed_xml"
    else:
        try:
            payload = json.loads(fetched["body"])
        except (json.JSONDecodeError, UnicodeDecodeError):
            records = []
            parse_error = "json_malformed_payload"
        else:
            if not isinstance(payload, dict):
                records = []
                parse_error = "json_wrong_root"
            else:
                records = [{"source_id": source_id, "category": spec["category"],
                            "provenance": spec["provenance"],
                            "title": "kraken status " + json.dumps(payload.get("result", {}))[:120],
                            "origin_url": spec["url"],
                            "published_raw": None,
                            "observed_ms": fetched["observed_ms"],
                            "raw_sha256": hashlib.sha256(fetched["body"]).hexdigest()}]
    for rec in records:
        rec["terms_decision"] = spec["terms"]
        rec["dated"] = spec.get("dated", True)
    return {"source_id": source_id,
            # The exact bytes are handed back so the collector can archive them.
            # A digest alone is not reconstructible.
            "raw_body": {"body": fetched["body"],
                         "observed_ms": fetched["observed_ms"],
                         "status": fetched["status"]},
            "fetched": {"status": fetched["status"],
                        "content_type": fetched["content_type"],
                                                "attempts": fetched["attempts"]},
            "records": records, "parse_error": parse_error}


if __name__ == "__main__":
    lim = RateLimiter()
    report = {"harvested": {}, "quarantined": QUARANTINED}
    for sid in SOURCES:
        try:
            got = harvest(sid, limiter=lim)
            report["harvested"][sid] = {"records": len(got["records"]),
                                        "status": got["fetched"]["status"]}
        except FetchError as exc:
            report["harvested"][sid] = {"error": str(exc)}
    print(json.dumps(report, indent=1, sort_keys=True))

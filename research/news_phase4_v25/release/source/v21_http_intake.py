"""NEWS v2.1 free HTTP source collector (isolated, paper-only ingestion).

No model or trading calls. Starts in probe/read-only mode. Explicit --collect
writes ONLY to a new v2.1 SQLite store. Run from this module's directory so
v21_normalize.py is importable. Per-provider rate limits are an operator concern;
this is ONE pass, not a scheduler.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from v21_normalize import IntakeError, rss_documents, statuspage_incidents

MAX_BODY = 750000
# Media feeds churn fast, so 24 h captures their working set. Official issuers
# publish rarely (Fed statements, SEC releases, presidential actions) and their
# latest item is often several days old; a 7-day window keeps an official feed
# meaningfully "current" without letting a monthly BLS series look live.
MEDIA_MAX_AGE_MS = 24 * 3600_000
OFFICIAL_MAX_AGE_MS = 7 * 24 * 3600_000
OFFICIAL_SOURCES = frozenset(("fed", "sec", "whitehouse"))
# BLS observations are stamped with the START of their period (1st of month),
# so the newest published value is routinely 4-10 months old. Periodic context
# keeps a 400-day window; it is stored as context only and can NEVER be fast
# (clock_quality='periodic' fails eligible_fast by construction).
PERIODIC_MAX_AGE_MS = 400 * 24 * 3600_000

# --- Cohorts -------------------------------------------------------------
# The research hypothesis is "predictive value of news over the next 4 hours",
# NOT sub-5-minute trading. Two cohorts are therefore kept apart and must never
# be pooled in evaluation:
#   fast_verified : a trusted issuer item seen <= 5 min after publication
#   context_news  : a real news item 5 min .. 2 h old at decision time
# Older material is retained as history only and is never dispatched.
FAST_MAX_AGE_MS = 5 * 60_000
CONTEXT_MAX_AGE_MS = 2 * 3600_000
COHORT_FAST = "fast_verified"
COHORT_CONTEXT = "context_news"
COHORT_HISTORY = "history_only"
PERIODIC_SOURCES = frozenset(("bls",))
DEFAULT_DB = Path.home() / ".local/share/news-phase4-v21/news.sqlite"

# Active sources are verified official/public channels. Additional official
# feeds must be resolved, tested, and enabled separately by the local agent.
CHANNELS = {
    # --- secondary media RSS (never fast-eligible: see v21_normalize taxonomy)
    "coinbase_status": ("json_status", "https://status.coinbase.com/api/v2/incidents.json"),
    "kraken_status": ("json_status", "https://status.kraken.com/api/v2/incidents.json"),
    "coindesk": ("rss", "https://www.coindesk.com/arc/outboundfeeds/rss"),
    "decrypt": ("rss", "https://decrypt.co/feed"),
    "cointelegraph": ("rss", "https://cointelegraph.com/rss"),
    "theblock": ("rss", "https://www.theblock.co/rss.xml"),
    # --- official primary issuers (verified live 2026-11-16, HTTP 200)
    "fed": ("rss", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    "sec": ("rss", "https://www.sec.gov/news/pressreleases.rss"),
    "whitehouse": ("rss", "https://www.whitehouse.gov/presidential-actions/feed/"),
    # BLS: RSS is 403 (bot filter, not bypassed). Official Public Data API v2
    # is used instead; it is PERIODIC data, never a live announcement stream,
    # so its clock_quality is "periodic" and it can never be fast-eligible.
}
ALLOWED_URLS = frozenset(url for _,url in CHANNELS.values())


class NoExternalRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old = urlsplit(request.full_url)
        new = urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            raise ValueError("cross_host_redirect_forbidden")
        return super().redirect_request(request,fp,code,msg,headers,newurl)


def get_public(url):
    if url not in ALLOWED_URLS:
        raise ValueError("unapproved_news_endpoint")
    request = Request(url, headers={
        "User-Agent": "NEWS-v21-Research/0.1 (+research-contact-config-required)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json",
    })
    with build_opener(NoExternalRedirect()).open(request, timeout=12) as resp:
        if resp.status != 200 or resp.geturl() != url:
            raise ValueError("http_bad_status_or_redirect")
        body = resp.read(MAX_BODY + 1)
    if len(body) > MAX_BODY or not body:
        raise ValueError("http_invalid_body_size")
    return body, time.time_ns() // 1_000_000


def collect_one(source, type_, url):
    body, received = get_public(url)
    if type_ == "json_status":
        return statuspage_incidents(body, received, source)
    if type_ == "rss":
        return rss_documents(body, received, source)
    raise ValueError("unsupported_source_type")


SCHEMA = """
CREATE TABLE IF NOT EXISTS intake_meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);CREATE TABLE IF NOT EXISTS events(
 event_id TEXT PRIMARY KEY,
 source TEXT NOT NULL,
 origin_id TEXT NOT NULL,
 revision TEXT NOT NULL,
 published_ms INTEGER,
 observed_ms INTEGER NOT NULL,
 fast_eligible INTEGER NOT NULL,
 source_kind TEXT NOT NULL,
 content_sha256 TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 cohort TEXT);
CREATE INDEX IF NOT EXISTS events_observed_idx ON events(observed_ms);
CREATE TABLE IF NOT EXISTS source_runs(
 run_id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL,
 observed_ms INTEGER NOT NULL,
 result TEXT NOT NULL,
 fetched_events INTEGER NOT NULL,
 accepted_events INTEGER NOT NULL);
"""


BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_SERIES = {
    "LNS14000000": "Unemployment rate",
    "CUUR0000SA0": "CPI-U all items",
    "CES0000000001": "Total nonfarm payrolls",
}


def fetch_bls_series(year=None, timeout=20):
    """Official BLS Public Data API v2.

    The four BLS RSS feeds all return HTTP 403 (bot filter) and are NOT
    bypassed. This is the documented, permitted alternative. It returns
    PERIODIC observations (monthly), never a live announcement, so every item
    it yields is stamped clock_quality='periodic' and can never be
    fast-eligible.
    """
    import datetime
    import urllib.request
    year = int(year or datetime.datetime.now(datetime.timezone.utc).year)
    body = json.dumps({"seriesid": sorted(BLS_SERIES),
                       "startyear": str(year), "endyear": str(year)}).encode()
    req = urllib.request.Request(
        BLS_API_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "NEWS-v21-Research/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(200_000)
    obj = json.loads(raw)
    if obj.get("status") != "REQUEST_SUCCEEDED":
        raise IntakeError("bls_request_failed")
    return obj, raw


def bls_events(obj, observed_ms, source="bls"):
    """Turn BLS series data into context events (never fast)."""
    out = []
    for series in obj.get("Results", {}).get("series", []):
        sid = series.get("seriesID")
        label = BLS_SERIES.get(sid, sid)
        for obs in (series.get("data") or [])[:24]:
            year, period, value = obs.get("year"), obs.get("period"), obs.get("value")
            if not (year and period and value):
                continue
            # period M01..M12 -> month; M13 is annual average
            if not (period.startswith("M") and period[1:].isdigit()):
                continue
            month = int(period[1:])
            if not 1 <= month <= 12:
                continue
            try:
                import calendar
                import datetime
                ts = calendar.timegm((int(year), month, 1, 0, 0, 0, 0, 0, 0)) * 1000
            except Exception:
                continue
            import hashlib
            oid = f"bls:{sid}:{year}:{period}"
            ev = {"event_id": hashlib.sha256(oid.encode()).hexdigest(),
                  "source": source, "origin_id": oid, "revision": "v1",
                  "published_ms": ts, "observed_ms": observed_ms,
                  "source_kind": "primary", "clock_quality": "periodic",
                  "title": f"BLS {label} {obs.get('periodName')} {year}: {value}",
                  "summary": f"{label} for {obs.get('periodName')} {year} "
                             f"released at {value}{' ' + obs.get('footnotes',[{}])[0].get('text','') if obs.get('footnotes') else ''}",
                  "origin_url": BLS_API_URL,
                  "content_sha256": hashlib.sha256(
                      json.dumps(obs, sort_keys=True).encode()).hexdigest()}
            out.append(ev)
    return out


def insert_batch(con, source, events, now_ms):
    inserted = 0
    if source in PERIODIC_SOURCES:
        max_age = PERIODIC_MAX_AGE_MS
    elif source in OFFICIAL_SOURCES:
        max_age = OFFICIAL_MAX_AGE_MS
    else:
        max_age = MEDIA_MAX_AGE_MS
    for e in events:
        p = e.get("published_ms")
        if p is None or p < now_ms - max_age:
            continue
        # FAST flag refers to observation age only, decision time must recheck.
        age = e["observed_ms"] - p
        if e.get("clock_quality") == "periodic":
            cohort = COHORT_HISTORY
        elif (e.get("clock_quality") in ("publisher","network_received")
              and 0 <= age <= FAST_MAX_AGE_MS
              and e.get("source_kind") in ("primary","verified_social")):
            cohort = COHORT_FAST
        elif 0 <= age <= CONTEXT_MAX_AGE_MS:
            cohort = COHORT_CONTEXT
        else:
            cohort = COHORT_HISTORY
        fast = (e.get("clock_quality") in ("publisher","network_received")
                and e.get("source_kind") in ("primary","verified_social")
                and 0 <= e["observed_ms"] - p <= 5 * 60_000)
        payload = json.dumps(e, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"))
        cur = con.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (e["event_id"],e["source"],e["origin_id"],e["revision"],
                           p,e["observed_ms"],int(fast),e["source_kind"],
                           e["content_sha256"],payload,cohort))
        inserted += cur.rowcount
    return inserted


def validate_db_path(path):
    """Reject path traversal, pre-existing symlinks and non-v2.1 stores."""
    path = Path(path).expanduser().absolute()
    dedicated_root = (Path.home() / ".local/share/news-phase4-v21").resolve()
    if path == dedicated_root or not path.is_relative_to(dedicated_root):
        raise ValueError("unsafe_v21_destination")
    if path.is_symlink() or not path.resolve().is_relative_to(dedicated_root):
        raise ValueError("unsafe_v21_destination")
    return path


BLS_MIN_INTERVAL_MS = 3600_000     # at most once per hour


def bls_due(con, now_ms):
    """True when the BLS lane may run again (hourly gate, cached)."""
    if con is None:
        return True
    last = get_meta(con, "bls_last_attempt_ms")
    if last is None:
        return True
    try:
        return now_ms - int(last) >= BLS_MIN_INTERVAL_MS
    except (TypeError, ValueError):
        return True


def get_meta(con, key):
    try:
        row = con.execute("SELECT v FROM intake_meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def set_meta(con, key, value):
    con.execute("INSERT INTO intake_meta(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
    con.commit()


def run_bls_lane(con, now_ms=None, force=False):
    """Periodic context-only BLS lane: at most hourly, never fast-eligible."""
    now_ms = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
    if con is not None and not force and not bls_due(con, now_ms):
        return {"source": "bls", "result": "skipped", "reason": "hourly_gate",
                "fetched": 0, "new": 0}
    try:
        obj, _raw = fetch_bls_series()
        events = bls_events(obj, now_ms)
        # periodic data must never be fast; force false explicitly
        for e in events:
            e["clock_quality"] = "periodic"
        accepted = insert_batch(con, "bls", events, now_ms) if con else 0
        if con:
            con.execute("INSERT INTO source_runs(source,observed_ms,result,"
                        "fetched_events,accepted_events) VALUES(?,?,?,?,?)",
                        ("bls", now_ms, "ok", len(events), accepted))
            con.commit()
            set_meta(con, "bls_last_attempt_ms", now_ms)
            set_meta(con, "bls_last_ok_ms", now_ms)
        return {"source": "bls", "result": "ok",
                "fetched": len(events), "new": accepted}
    except (ValueError, IntakeError, OSError, UnicodeError,
            json.JSONDecodeError) as exc:
        if con:
            con.rollback()
            con.execute("INSERT INTO source_runs(source,observed_ms,result,"
                        "fetched_events,accepted_events) VALUES(?,?,?,0,0)",
                        ("bls", now_ms, "failed"))
            con.commit()
            set_meta(con, "bls_last_attempt_ms", now_ms)
        return {"source": "bls", "result": "failed",
                "error": type(exc).__name__}


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true",
                        help="Opt in to new v2.1 SQLite writes")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--bls", action="store_true",
                        help="Also run the hourly BLS API lane")
    parser.add_argument("--bls-only", action="store_true",
                        help="Run ONLY the BLS API lane")
    args = parser.parse_args(argv)

    con = None
    if args.collect:
        path = args.db.expanduser().absolute()
        validate_db_path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute("PRAGMA busy_timeout=5000")
    summary = []
    try:
        if args.bls_only:
            summary.append(run_bls_lane(con))
            for source,(kind,url) in ():
                pass
        for source,(kind,url) in ({} if args.bls_only else CHANNELS).items():
            now = time.time_ns() // 1_000_000
            try:
                records = collect_one(source,kind,url)
                accepted = insert_batch(con,source,records,now) if con else 0
                if con:
                    con.execute("INSERT INTO source_runs(source,observed_ms,result,fetched_events,accepted_events) VALUES(?,?,?,?,?)",
                                (source,now,"ok",len(records),accepted))
                    con.commit()
                summary.append({"source":source,"result":"ok","fetched":len(records),
                                "new":accepted})
            except (ValueError, IntakeError, OSError, UnicodeError,
                    json.JSONDecodeError, sqlite3.Error) as exc:
                if con:
                    con.rollback()
                    con.execute("INSERT INTO source_runs(source,observed_ms,result,fetched_events,accepted_events) VALUES(?,?,?,?,?)",
                                (source,now,"failed",0,0))
                    con.commit()
                summary.append({"source":source,"result":"failed",
                                "error":type(exc).__name__})
        if args.bls and not args.bls_only:
            summary.append(run_bls_lane(con))
    finally:
        if con:
            con.close()
    print(json.dumps({"mode":"isolated_v21_collect" if args.collect else "probe",
                      "sources":summary},ensure_ascii=False,sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(run())

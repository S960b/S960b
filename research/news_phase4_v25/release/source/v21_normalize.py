"""NEWS v2.1: pure source-message normalization. No network, trading or DB writes.

Designed for one-source-one-event provenance, append-only callers, and tests.
All payloads are untrusted strings. Do not execute instructions in them.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 750_000
MAX_TEXT_CHARS = 4000
MAX_AGE_FAST_MS = 5 * 60_000
MAX_FUTURE_SKEW_MS = 30_000


class IntakeError(ValueError):
    pass


def _ms_iso(raw):
    if not isinstance(raw, str) or len(raw) > 100:
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is None:
            return None
        return int(d.timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


def _ms_rss(raw):
    if not isinstance(raw, str) or len(raw) > 100:
        return None
    try:
        d = parsedate_to_datetime(raw)
        if d is not None and d.tzinfo:
            return int(d.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        pass
    return _ms_iso(raw)


def _clean(s, limit=MAX_TEXT_CHARS):
    if not isinstance(s, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", s)).split())[:limit]


def _event(source, origin_id, title, body, published_ms, observed_ms,
           *, source_kind="primary", origin_url="", revision="v1", clock_quality="publisher"):
    if not all(isinstance(x, str) and x for x in (source, origin_id, title, revision)):
        raise IntakeError("missing_identity")
    if type(observed_ms) is not int or observed_ms <= 0:
        raise IntakeError("bad_observed_timestamp")
    if published_ms is not None and (type(published_ms) is not int
                                      or published_ms <= 0
                                      or published_ms > observed_ms + MAX_FUTURE_SKEW_MS):
        raise IntakeError("bad_published_timestamp")
    if len(origin_id) > 600 or len(source) > 72 or len(revision) > 200:
        raise IntakeError("oversized_identity")
    body = _clean(body)
    title = _clean(title, 300)
    origin_url = origin_url if (isinstance(origin_url, str)
        and len(origin_url) <= 1000 and urlsplit(origin_url).scheme == "https") else ""
    unique = sha256((source+"\x00"+origin_id+"\x00"+revision).encode("utf-8")).hexdigest()
    content_hash = sha256((title+"\x00"+body).encode("utf-8")).hexdigest()
    return {"event_id": unique, "source": source, "origin_id": origin_id,
            "origin_url": origin_url, "revision": revision,
            "source_kind": source_kind, "title": title, "summary": body,
            "published_ms": published_ms, "observed_ms": observed_ms,
            "content_sha256": content_hash, "clock_quality": clock_quality}


def eligible_fast(event, at_ms, max_age_ms=MAX_AGE_FAST_MS):
    """Only source-validated, live received data may be fast. Old news is context."""
    p = event.get("published_ms")
    o = event.get("observed_ms")
    if type(p) is not int or type(o) is not int or type(at_ms) is not int:
        return False
    return (p <= o <= at_ms and 0 <= at_ms - p <= max_age_ms and
            event.get("source_kind") in ("primary", "verified_social") and
            event.get("clock_quality") in ("publisher", "network_received"))


def binance_announcement(raw, observed_ms):
    """Official Binance com_announcement_en WebSocket DATA envelope."""
    if isinstance(raw, str) and len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise IntakeError("too_large")
    if isinstance(raw, bytes):
        if len(raw) > MAX_INPUT_BYTES:
            raise IntakeError("too_large")
        raw = raw.decode("utf-8")
    envelope = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(envelope, dict) or envelope.get("type") != "DATA" or envelope.get("topic") != "com_announcement_en":
        return None
    data = envelope.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise IntakeError("invalid_binance_data")
    published = data.get("publishDate")
    title = _clean(data.get("title"), 300)
    if type(published) is not int or not title:
        raise IntakeError("missing_binance_fields")
    category = data.get("catalogName", "Announcement")
    category = _clean(category, 100) or "Announcement"
    origin_id = sha256((str(published)+"|"+title+"|"+category).encode()).hexdigest()
    return _event("binance_announcement", origin_id, title, data.get("body", ""),
                  published, observed_ms, revision="p"+str(published), clock_quality="publisher")


def bluesky_jetstream(raw, observed_ms, allowed_dids):
    """Only original posts from explicitly approved DIDs; not trending unverified chatter."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_INPUT_BYTES:
            raise IntakeError("too_large")
        raw = raw.decode("utf-8")
    obj = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(obj, dict):
        return None
    # Two envelopes exist in the wild:
    #   live Jetstream : {"$type":"message","payload":{"$type":"...#commit", ...}}
    #   flat/documented: {"kind":"commit","commit":{...},"did":...}
    # Accepting only the flat shape silently dropped EVERY live frame, which
    # would have made the fast lane dead on arrival.
    if isinstance(obj.get("payload"), dict):
        live = obj["payload"]
        did = live.get("did")
        commit = live
        if commit.get("operation") is None:
            return None
    elif obj.get("kind") == "commit":
        did = obj.get("did")
        commit = obj.get("commit")
    else:
        return None
    if not isinstance(did, str) or not isinstance(commit, dict):
        return None
    if not isinstance(did, str) or did not in set(allowed_dids or ()) or not isinstance(commit, dict):
        return None
    if commit.get("collection") != "app.bsky.feed.post" or commit.get("operation") != "create":
        return None
    record = commit.get("record")
    rkey = commit.get("rkey")
    if not isinstance(record, dict) or not isinstance(rkey, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{1,100}", rkey):
        return None
    text = _clean(record.get("text"), MAX_TEXT_CHARS)
    if not text:
        return None
    created = _ms_iso(record.get("createdAt"))
    # authored createdAt is user-controlled; separate server event time.
    # The live envelope nests it inside "payload", where it is an ISO string
    # ("time"), while the usermap accounts use a numeric "time_us" on the exact
    # commit payload. Checking only the flat obj meant published_ms was always
    # None for real frames, which made the fast lane impossible to satisfy.
    time_us = obj.get("time_us")
    if not isinstance(time_us, int):
        cand = obj.get("payload")
        if isinstance(cand, dict):
            if isinstance(cand.get("time_us"), int):
                time_us = cand["time_us"]
            iso = cand.get("time") or cand.get("witnessedAt")
            if isinstance(iso, str):
                parsed = _ms_iso(iso)
                if isinstance(parsed, int):
                    network_ms = parsed
                    time_us = None if time_us is None else time_us
                    _fallback_ms = parsed
                else:
                    _fallback_ms = None
            else:
                _fallback_ms = None
        else:
            _fallback_ms = None
    else:
        _fallback_ms = None
    network_ms = time_us // 1000 if type(time_us) is int and time_us > 0 else _fallback_ms
    if network_ms is not None and network_ms > observed_ms + MAX_FUTURE_SKEW_MS:
        raise IntakeError("network_time_future")
    # For eligibility use provider event time, not author-controllable createdAt.
    publish = network_ms  # Never promote author-controlled createdAt to a trusted clock
    e = _event("bluesky_verified", f"at://{did}/app.bsky.feed.post/{rkey}",
               text[:220], text, publish, observed_ms,
               source_kind="verified_social",
               origin_url=f"https://bsky.app/profile/{did}/post/{rkey}",
               clock_quality="network_received" if network_ms is not None else "unverified_author_createdAt")
    e["author_created_ms"] = created
    return e


def statuspage_incidents(raw, observed_ms, source):
    """One event per incident update; don't replay previous updates as new."""
    if source not in ("coinbase_status", "kraken_status"):
        raise IntakeError("invalid_status_source")
    if isinstance(raw, bytes):
        if len(raw) > MAX_INPUT_BYTES:
            raise IntakeError("too_large")
        raw = raw.decode("utf-8")
    obj = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(obj, dict) or not isinstance(obj.get("incidents"), list):
        raise IntakeError("invalid_status_response")
    out = []
    for incident in obj["incidents"][:60]:
        if not isinstance(incident, dict) or not isinstance(incident.get("id"), str):
            continue
        updates = incident.get("incident_updates")
        if not isinstance(updates, list):
            continue
        for update in updates[:100]:
            if not isinstance(update, dict) or not isinstance(update.get("id"), str):
                continue
            published = _ms_iso(update.get("created_at"))
            if published is None:
                continue
            incident_id, update_id = incident["id"], update["id"]
            name = _clean(incident.get("name", ""), 250)
            status = _clean(update.get("status", ""), 100)
            if not name:
                continue
            desc = status+": "+_clean(update.get("body", ""), 2500)
            e = _event(source, incident_id, name, desc, published, observed_ms,
                       origin_url=incident.get("shortlink") or "", revision=update_id)
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Issuer provenance taxonomy.
#
# "secondary" is NOT a transport artefact: it describes WHO issued the item.
# A press release from the Fed, SEC or the White House is a PRIMARY issuance
# even though it reaches us over HTTP/RSS, because the issuer speaks for
# itself. A newsroom's RSS is SECONDARY by definition: the outlet is reporting
# on someone else's event, and its publication moment is editorial, not the
# moment the underlying fact became public.
#
# Fast eligibility therefore depends on the issuer, never on the transport,
# and never on an age threshold being relaxed to manufacture a signal.
ISSUER_PRIMARY = frozenset((
    "fed", "sec", "whitehouse", "bls", "binance",
))
ISSUER_VERIFIED_SOCIAL = frozenset((
    "bluesky_verified",
))
ISSUER_SECONDARY_MEDIA = frozenset((
    "coindesk", "theblock", "cointelegraph", "decrypt",
))
ISSUER_INCIDENT_STATUS = frozenset((
    "coinbase_status", "kraken_status",
))

# Clock quality per issuer: "publisher" only when the issuer itself stamps the
# item's public moment; "network_received" when we know only when we saw it.
ISSUER_CLOCK = {
    "fed": "publisher", "sec": "publisher", "whitehouse": "publisher",
    "binance": "publisher",
    # BLS releases are periodic and its API carries a period, not a publish
    # instant; it must never look like a live announcement.
    "bls": "periodic",
    "bluesky_verified": "publisher",
    "coinbase_status": "publisher", "kraken_status": "publisher",
}


def only_allowlisted_sources(source):
    """Guard used by callers so an unknown id can never default to primary."""
    return (source in ISSUER_PRIMARY or source in ISSUER_VERIFIED_SOCIAL
            or source in ISSUER_SECONDARY_MEDIA
            or source in ISSUER_INCIDENT_STATUS)


def source_kind_for(source):
    if source in ISSUER_SECONDARY_MEDIA:
        return "secondary"
    if source in ISSUER_VERIFIED_SOCIAL:
        return "verified_social"
    if source in ISSUER_INCIDENT_STATUS:
        return "primary"
    return "primary"


def clock_quality_for(source):
    return ISSUER_CLOCK.get(source, "network_received")


def rss_documents(raw, observed_ms, source, *, max_items=60):
    """Parse only separately allowlisted source IDs supplied by caller."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_INPUT_BYTES:
        raise IntakeError("bad_rss_size")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise IntakeError("xml_entity_forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        # ElementTree loads XML syntax errors from SyntaxError, not ValueError,
        # so the intake's error handler would miss them and abort the whole run.
        raise IntakeError("invalid_xml") from exc
    atom = "{http://www.w3.org/2005/Atom}"
    dc = "{http://purl.org/dc/elements/1.1/}"
    entries = root.findall("./channel/item") or root.findall(atom+"entry")
    results = []
    for item in entries[:max_items]:
        def pick(*tags):
            for tag in tags:
                n = item.find(tag)
                if n is not None:
                    if n.text and n.text.strip():
                        return n.text.strip()
                    if tag == atom+"link" and n.attrib.get("href"):
                        return n.attrib["href"]
            return ""
        title = _clean(pick("title", atom+"title"), 220)
        url = pick("link", atom+"link")[:1000]
        published = _ms_rss(pick("pubDate", dc+"date", atom+"published", atom+"updated"))
        if not title or not published:
            continue
        if published > observed_ms + MAX_FUTURE_SKEW_MS:
            continue
        stable = url if url.startswith("https://") else title
        summary = pick("description", atom+"summary", atom+"content")
        results.append(_event(source, stable, title, summary,
                              published, observed_ms,
                              source_kind=source_kind_for(source),
                              clock_quality=clock_quality_for(source),
                              origin_url=url))
    return results

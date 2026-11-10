"""Read only *previously sealed* Phase4 forward news/market evidence.

No network, imports from Phase4, live writes, API keys or hindsight features.
Only future Phase4 events collected after v2 activation may be selected.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import pstdev

from v2_core import V2Error, canonical, require

HOUR_MS = 3_600_000


def market_features(source_root, market_hash, cutoff_ms):
    require(type(market_hash) is str and len(market_hash) == 64 and
            all(c in "0123456789abcdef" for c in market_hash),
            "bad_market_digest")
    root = Path(source_root)
    path = root / "evidence" / (market_hash + ".json.gz")
    require(not path.is_symlink() and path.is_file(),
            "sealed_market_not_found")
    with path.open("rb") as handle:
        packed = handle.read(100_001)
    require(0 < len(packed) <= 100_000, "market_blob_too_large")
    try:
        raw = gzip.decompress(packed)
    except (EOFError, OSError) as exc:
        raise V2Error("market_gzip_invalid") from exc
    require(len(raw) < 750_000 and
            hashlib.sha256(raw).hexdigest() == market_hash,
            "market_hash_mismatch")
    try:
        market = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise V2Error("market_json_invalid") from exc
    require(type(market) is dict and market.get("symbol") == "BTCUSDT"
            and market.get("interval") == "1h", "market_type_invalid")
    data = market.get("closed_candles")
    require(type(data) is list and len(data) == 26, "market_candles_invalid")
    closes, volumes, prior = [], [], None
    for row in data:
        require(type(row) is list and len(row) == 4, "market_row_invalid")
        opened, closed, price, volume = row
        require(type(opened) is int and type(closed) is int and
                opened % HOUR_MS == 0 and
                closed == opened + HOUR_MS - 1 and
                (prior is None or opened == prior + HOUR_MS) and
                closed + 3000 <= cutoff_ms,
                "future_or_gapped_market_candles")
        try:
            p, v = float(price), float(volume)
        except (ValueError, TypeError, OverflowError) as exc:
            raise V2Error("market_numbers_invalid") from exc
        require(math.isfinite(p) and p > 0 and math.isfinite(v)
                and v >= 0, "market_numbers_invalid")
        closes.append(p)
        volumes.append(v)
        prior = opened
    require(cutoff_ms - data[-1][1] <= HOUR_MS + 3000,
            "stale_market_candles")
    log_rets = [math.log(b/a) for a,b in zip(closes,closes[1:])]
    volbase = sum(volumes[-25:-1])/24
    features = {
        "return_1h": closes[-1]/closes[-2]-1,
        "return_4h": closes[-1]/closes[-5]-1,
        "return_24h": closes[-1]/closes[-25]-1,
        "realized_vol_24h": pstdev(log_rets[-24:]),
        "volume_ratio_1h_to_prev24h": (
            volumes[-1]/volbase if volbase > 0 else None)
    }
    return features


def candidates(source_root, now_ms, start_ms):
    require(type(now_ms) is int and type(start_ms) is int and
            now_ms >= start_ms > 0, "invalid_source_clock")
    source_root = Path(source_root).expanduser().absolute()
    dbpath = source_root / "news.sqlite"
    require(not source_root.is_symlink() and not dbpath.is_symlink()
            and dbpath.is_file(), "phase4_source_unavailable")
    con = sqlite3.connect(dbpath.as_uri()+"?mode=ro", uri=True,
                          timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        version = con.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        require(version == ("4",), "phase4_schema_incompatible")
        rows = con.execute("""
            SELECT e.event_id,e.cutoff_ms,n.news_json,n.news_digest,
                   m.digest,m.available_ms,m.observed_ms,n.first_seen_ms
            FROM events e
            JOIN claimed_events c ON c.event_id=e.event_id
            JOIN news_observations n ON n.fingerprint=c.fingerprint
            JOIN features m ON m.event_id=e.event_id AND m.source='market'
            WHERE e.cutoff_ms>? AND e.cutoff_ms BETWEEN ? AND ?
            ORDER BY e.cutoff_ms ASC,e.event_id ASC LIMIT 100
        """, (start_ms, now_ms-1_800_000, now_ms)).fetchall()
    finally:
        con.close()
    result = []
    for eid,cutoff,serialized,digest,market_hash,avail,observed,first in rows:
        require(0 < first <= cutoff and avail <= cutoff and
                observed <= cutoff, "source_future_leakage")
        require(hashlib.sha256(serialized.encode()).hexdigest() == digest,
                "news_hash_mismatch")
        news = json.loads(serialized)
        require(type(news) is dict and
                news.get("first_http_seen_ms") == first,
                "news_first_seen_inconsistent")
        features = market_features(source_root, market_hash, cutoff)
        result.append({"event_id":eid, "cutoff_ms":cutoff,
                       "news":news, "market":features,
                       "news_hash":digest, "market_hash":market_hash})
    return result

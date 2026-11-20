#!/usr/bin/env python3
"""NEWS v2.6 RSS-only collector integration, UNARMED and opt-in.

Uses the tested v24 collector's immutable raw witness / observations / event
schema. The 14-feed normalizer is the only parser for new feeds. This module
is not the production entrypoint until the agent validates it in an isolated DB.
No model calls, no series admission, no network access on import.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # research/news_phase4_v25
RSS_MODULE = ROOT / "rss_expansion" / "rss14_adapter.py"
spec = importlib.util.spec_from_file_location("rss14_adapter", RSS_MODULE)
if spec is None or spec.loader is None:
    raise RuntimeError("rss14_module_missing")
RSS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RSS)

import v24_adapters as LEGACY_AD
import v24_collector as LEGACY_COLLECTOR

SOURCES = {
    sid: {"url": url, "category": category, "provenance": provenance,
          "asset_scope": scope, "format": "rss",
          "terms": "RSS metadata; separate source-use review applies"}
    for sid, (url, category, provenance, scope) in RSS.RSS14.items()
}
RSS.validate_roster()

class RSS14Adapter:
    """Adapter compatible with v24_collector.tick; never changes old globals."""
    SOURCES = SOURCES
    FetchError = LEGACY_AD.FetchError
    RateLimiter = LEGACY_AD.RateLimiter

    @staticmethod
    def harvest(source_id, *, limiter, opener=None):
        if source_id not in SOURCES:
            raise LEGACY_AD.FetchError("unknown_source")
        source = SOURCES[source_id]
        fetched = LEGACY_AD.fetch(
            source["url"], limiter=limiter, opener=opener,
            accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
        try:
            normalized = RSS.parse_feed(
                fetched["body"], source_id=source_id,
                observed_ms=fetched["observed_ms"])
            records = normalized["records"]
            error = None
        except RSS.FeedError as exc:
            records = []
            error = "rss14_" + str(exc)
        for record in records:
            record["terms_decision"] = source["terms"]
            record["dated"] = True
        return {
            "source_id": source_id,
            "raw_body": {"body": fetched["body"], "observed_ms": fetched["observed_ms"],
                         "status": fetched["status"]},
            "fetched": {"status": fetched["status"],
                        "content_type": fetched["content_type"],
                        "attempts": fetched["attempts"]},
            "records": records,
            "parse_error": error,
        }

AD = RSS14Adapter()

def tick(*, con, limiter, sources=None, keep_raw=True, raw_dir=None):
    """Use explicit adapter injection; safe alongside other collector instances.

    This wrapper remains unarmed and opt-in. The legacy default remains AD.
    """
    return LEGACY_COLLECTOR.tick(
        con=con, limiter=limiter, sources=sources or list(SOURCES),
        keep_raw=keep_raw, raw_dir=raw_dir, adapter=AD)

def selftest():
    assert len(SOURCES) == 14
    assert all(s["format"] == "rss" for s in SOURCES.values())
    assert "kraken_status" not in SOURCES
    assert SOURCES["solana_status"]["asset_scope"] == "SOL"
    assert RSS_MODULE.is_file()
    assert LEGACY_COLLECTOR.AD is LEGACY_AD
    print("RSS14 COLLECTOR INTEGRATION SELFTEST OK")

if __name__ == "__main__":
    selftest()

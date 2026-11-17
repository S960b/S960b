#!/usr/bin/env python3
"""NEWS v2.4 — parser-side adapter conformance kit (recorded fixtures only).

Every payload here is a RECORDED SYNTHETIC fixture written to disk. Nothing in
this module opens a socket. The point is to pin, before any feed exists, what a
conforming adapter must produce for each of the seven categories, how it must
parse timestamps (including offsets), and how it must refuse.

A conformance kit is a contract, not a proof of authenticity: see
NEWS_V24_AMENDMENT_2.md on why `content_sha256` is an integrity checksum and
not a provenance guarantee.
"""
import json
from pathlib import Path

import v24_sources as S

FIXTURE_DIR = Path(__file__).resolve().parent / "v24_fixtures"

# One recorded payload per category, shaped like the machine-readable feed we
# would consume. `observed_ms` and `published_ms` are absolute, so a fixture
# replay is deterministic.
FIXTURES = {
    "crypto": {"source_id": "bin", "category": "crypto",
               "provenance": "primary", "title": "Funding rate flips positive",
               "origin_url": "https://www.example-exchange.com/news/1",
               "published_ms": 1_800_000_000_000,
               "observed_ms": 1_800_000_030_000},
    "macro": {"source_id": "bls", "category": "macro",
              "provenance": "primary", "title": "Payrolls beat expectations",
              "origin_url": "https://www.example-stats.gov/release/2",
              "published_ms": 1_800_000_100_000,
              "observed_ms": 1_800_000_120_000},
    "monetary_policy": {"source_id": "fed", "category": "monetary_policy",
                        "provenance": "primary",
                        "title": "FOMC holds rates steady",
                        "origin_url": "https://www.example-cb.org/press/3",
                        "published_ms": 1_800_000_200_000,
                        "observed_ms": 1_800_000_220_000},
    "geopolitics": {"source_id": "wire", "category": "geopolitics",
                    "provenance": "media_wire",
                    "title": "Border talks resume in Geneva",
                    "origin_url": "https://www.example-wire.net/story/4",
                    "published_ms": 1_800_000_300_000,
                    "observed_ms": 1_800_000_400_000},
    "sanctions": {"source_id": "ofac", "category": "sanctions",
                  "provenance": "primary",
                  "title": "Treasury sanctions two crypto mixers",
                  "origin_url": "https://www.example-treasury.gov/press/5",
                  "published_ms": 1_800_000_400_000,
                  "observed_ms": 1_800_000_450_000},
    "energy": {"source_id": "eia", "category": "energy",
               "provenance": "primary",
               "title": "Crude inventories fall sharply",
               "origin_url": "https://www.example-energy.gov/data/6",
               "published_ms": 1_800_000_500_000,
               "observed_ms": 1_800_000_560_000},
    "regulatory": {"source_id": "sec", "category": "regulatory",
                   "provenance": "primary",
                   "title": "SEC proposes custody rule change",
                   "origin_url": "https://www.example-sec.gov/rule/7",
                   "published_ms": 1_800_000_600_000,
                   "observed_ms": 1_800_000_640_000},
}

SPECS = [
    S.SourceSpec("bin", "crypto", "primary", 120),
    S.SourceSpec("bls", "macro", "primary", 300),
    S.SourceSpec("fed", "monetary_policy", "primary", 300),
    S.SourceSpec("wire", "geopolitics", "media_wire", 600),
    S.SourceSpec("ofac", "sanctions", "primary", 900),
    S.SourceSpec("eia", "energy", "primary", 900),
    S.SourceSpec("sec", "regulatory", "primary", 900),
]


def registry():
    return S.SourceRegistry(SPECS)


def parse_timestamp(value):
    """Accept epoch millis (int, not bool) or an ISO-8601 string with offset.

    An ISO string WITHOUT an offset is refused: "2026-11-17 10:00:00" is a
    local time whose meaning depends on the host, and a silent assumption there
    is exactly how a fixture becomes non-reproducible.
    """
    if isinstance(value, bool):
        raise S.AdapterError("timestamp_bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        import datetime
        text = value.strip()
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise S.AdapterError("timestamp_unparseable") from None
        if parsed.tzinfo is None:
            raise S.AdapterError("timestamp_without_offset")
        return int(parsed.timestamp() * 1000)
    raise S.AdapterError("timestamp_unsupported_type")


def record_from_fixture(payload):
    """Adapter contract: payload -> SourceRecord, or a named refusal."""
    for key in ("source_id", "category", "provenance", "title", "origin_url",
                "published_ms", "observed_ms"):
        if key not in payload:
            raise S.AdapterError("fixture_missing_field:" + key)
    published = parse_timestamp(payload["published_ms"])
    observed = parse_timestamp(payload["observed_ms"])
    title = payload["title"]
    return S.SourceRecord(
        source_id=payload["source_id"], category=payload["category"],
        provenance=payload["provenance"], published_ms=published,
        observed_ms=observed, title=title, origin_url=payload["origin_url"],
        content_sha256=S.content_digest(title, published))


def verify_all(now_ms=None):
    """Every fixture must validate, and the registry must cover all categories.

    When `now_ms` is None each fixture is validated at its own `observed_ms`,
    which is the only clock at which a recorded payload can honestly be judged:
    a fixed wall-clock instant would reject the older fixtures as stale and hide
    the shape checks behind an age check. A caller may still pass a fixed
    decision time to exercise the freshness rules.
    """
    reg = registry()
    out = {"ok": [], "refused": []}
    for category, payload in sorted(FIXTURES.items()):
        try:
            rec = record_from_fixture(payload)
            spec = reg.get(rec.source_id)
            if spec is None:
                raise S.AdapterError("unknown_source:" + rec.source_id)
            S.validate_record(rec, spec=spec,
                              now_ms=now_ms if now_ms is not None
                              else rec.observed_ms + 1_000)
            out["ok"].append({"category": category,
                              "digest": rec.content_sha256})
        except S.AdapterError as exc:
            out["refused"].append({"category": category, "reason": str(exc)})
    declared = reg.category_sources()
    out["categories_covered"] = sorted(c for c, ids in declared.items() if ids)
    return out


def write_fixtures(directory=None):
    """Persist the recorded payloads as immutable fixtures on disk."""
    target = Path(directory) if directory else FIXTURE_DIR
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for category, payload in sorted(FIXTURES.items()):
        path = target / f"{category}.json"
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
        written.append(str(path))
    return written


def load_fixtures(directory=None):
    """Read the fixtures back from disk; no regeneration, no repair."""
    target = Path(directory) if directory else FIXTURE_DIR
    out = {}
    for category in FIXTURES:
        path = target / f"{category}.json"
        if not path.is_file():
            raise S.AdapterError("fixture_missing:" + category)
        out[category] = json.loads(path.read_text(encoding="utf-8"))
    return out


if __name__ == "__main__":
    print(json.dumps(verify_all(), indent=1, sort_keys=True))

#!/usr/bin/env python3
"""NEWS v2.4 — source-broadening design, fixture-only adapter contract.

DESIGN AND CONTRACT ONLY. There is no network call in this module, no live
feed, and nothing here may write into v2.1 or v2.2. The point is to make the
source-coverage criticism testable: an adapter produces a normalised record, a
selector decides eligibility, and a fixture proves both behave as documented.

The critical design choice: **relevance must not be the BTC keyword gate**. The
existing gate drops a sanctions package or an oil shock because it never says
"bitcoin", which is exactly the regime a broader experiment wants to measure.
v2.4 therefore scores relevance BY CATEGORY, not by keyword.
"""
import hashlib
import json
from dataclasses import dataclass, field

PROTOCOL_VERSION = "prospective-v2.4-sources"

# Categories are declared up front. A record without a category is not
# eligible: an "other" bucket is how a source regime quietly drifts.
CATEGORIES = ("crypto", "macro", "monetary_policy", "geopolitics",
              "sanctions", "energy", "regulatory")

PROVENANCE_CLASSES = ("primary", "official_secondary", "media_wire")

# Freshness budget per category, in seconds. A source that cannot meet its
# budget is excluded rather than silently downgraded: a 40-minute-old "shock"
# is not a shock.
FRESHNESS_BUDGET_S = {
    "crypto": 120,
    "macro": 300,
    "monetary_policy": 300,
    "geopolitics": 600,
    "sanctions": 900,
    "energy": 900,
    "regulatory": 900,
}


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    category: str
    provenance: str
    freshness_s: int
    machine_readable: bool = True


@dataclass(frozen=True)
class SourceRecord:
    """One normalised item as an adapter must emit it."""
    source_id: str
    category: str
    provenance: str
    published_ms: int
    observed_ms: int
    title: str
    origin_url: str
    content_sha256: str
    raw: dict = field(default_factory=dict, compare=False)

    def to_json(self):
        return json.dumps({"source_id": self.source_id,
                           "category": self.category,
                           "provenance": self.provenance,
                           "published_ms": self.published_ms,
                           "observed_ms": self.observed_ms,
                           "title": self.title,
                           "origin_url": self.origin_url,
                           "content_sha256": self.content_sha256},
                          sort_keys=True)


class AdapterError(ValueError):
    pass


def validate_record(rec, *, spec, now_ms):
    """Adapter contract. Every rule is a refusal, never a silent repair."""
    if not isinstance(rec, SourceRecord):
        raise AdapterError("not_a_source_record")
    if rec.source_id != spec.source_id:
        raise AdapterError("source_id_mismatch")
    if rec.category not in CATEGORIES:
        raise AdapterError("unknown_category:" + str(rec.category))
    if rec.category != spec.category:
        raise AdapterError("category_mismatch:" + spec.source_id)
    if rec.provenance not in PROVENANCE_CLASSES:
        raise AdapterError("unknown_provenance:" + str(rec.provenance))
    if not (isinstance(rec.published_ms, int) and isinstance(rec.observed_ms, int)):
        raise AdapterError("timestamps_not_integer")
    if rec.observed_ms < rec.published_ms:
        # An item cannot be observed before it is published; that is a clock
        # bug or a forged feed, not a freshness detail.
        raise AdapterError("observed_before_published")
    lag_s = (rec.observed_ms - rec.published_ms) / 1000.0
    if lag_s > FRESHNESS_BUDGET_S[rec.category]:
        raise AdapterError("too_stale:" + rec.category + ":" + str(int(lag_s)))
    if not rec.title.strip():
        raise AdapterError("empty_title")
    if not (rec.origin_url.startswith("https://")
            or rec.origin_url.startswith("http://")):
        raise AdapterError("bad_origin_url")
    expected = content_digest(rec.title, rec.published_ms)
    if rec.content_sha256 != expected:
        raise AdapterError("content_digest_mismatch")
    if rec.observed_ms > now_ms:
        raise AdapterError("observed_in_future")
    return True


def content_digest(title, published_ms):
    return hashlib.sha256(
        (str(title).strip() + "|" + str(int(published_ms))).encode()).hexdigest()


def dedupe_key(rec):
    """Cross-source dedupe: the first 8 normalised title words.

    Deliberately crude and documented as such. A semantic dedupe would be a
    model call, and a model call inside ingestion is a leak vector.
    """
    import re
    norm = re.sub(r"[^a-z0-9 ]+", " ", rec.title.lower())
    return " ".join(w for w in norm.split() if len(w) > 2)[:80]


def select(records, *, now_ms, max_age_s=None, per_category_cap=None):
    """Eligibility and sampling, deterministic and reproducible.

    Ordering: (category priority as declared, published_ms older first,
    content_sha256). The hash tie-break means two identical stories arriving in
    the same millisecond still order the same way on every run.
    """
    accepted, rejected = [], []
    seen = set()
    counts = {}
    for rec in records:
        budget = FRESHNESS_BUDGET_S[rec.category]
        age_s = (now_ms - rec.published_ms) / 1000.0
        if age_s < 0:
            rejected.append((rec.content_sha256, "published_in_future"))
            continue
        if age_s > budget or (max_age_s is not None and age_s > max_age_s):
            rejected.append((rec.content_sha256, "stale"))
            continue
        key = dedupe_key(rec)
        if key in seen:
            rejected.append((rec.content_sha256, "duplicate"))
            continue
        if per_category_cap and counts.get(rec.category, 0) >= per_category_cap:
            rejected.append((rec.content_sha256, "category_cap"))
            continue
        seen.add(key)
        counts[rec.category] = counts.get(rec.category, 0) + 1
        accepted.append(rec)
    accepted.sort(key=lambda r: (CATEGORIES.index(r.category),
                                 r.published_ms, r.content_sha256))
    return accepted, rejected

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
    # A record may not self-declare a better provenance than its spec grants.
    if rec.provenance != spec.provenance:
        raise AdapterError("provenance_mismatch:" + spec.source_id)
    # bool is an int subclass in Python: a True timestamp would sail through
    # every comparison below, so it is rejected explicitly.
    for name in ("published_ms", "observed_ms"):
        value = getattr(rec, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise AdapterError("timestamps_not_integer:" + name)
    if rec.observed_ms < rec.published_ms:
        # An item cannot be observed before it is published; that is a clock
        # bug or a forged feed, not a freshness detail.
        raise AdapterError("observed_before_published")
    # TWO clocks, both enforced (stage-6 correction).
    #   transport lag  = observed - published : how late the wire was
    #   decision age   = now - published      : how stale the story is when the
    #                                            decision is actually taken
    # Checking only one of them let a record observed long after publication,
    # or one published days ago but polled just now, pass as "fresh".
    budget_s = FRESHNESS_BUDGET_S[rec.category]
    transport_lag_s = (rec.observed_ms - rec.published_ms) / 1000.0
    if transport_lag_s > budget_s:
        raise AdapterError("transport_lag_too_large:" + rec.category + ":"
                           + str(int(transport_lag_s)))
    decision_age_s = (now_ms - rec.published_ms) / 1000.0
    if decision_age_s > budget_s:
        raise AdapterError("too_stale:" + rec.category + ":"
                           + str(int(decision_age_s)))
    if rec.observed_ms > now_ms:
        raise AdapterError("observed_after_decision")
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


class SourceRegistry:
    """Declared sources. A record cannot self-declare its provenance.

    Stage-6 correction: without a registry a source id invented on the spot
    could claim `primary` provenance and skip every freshness rule it liked.
    The registry is the only place a SourceSpec lives; an unknown id is
    rejected, and a record whose declared category/provenance disagrees with
    its spec is rejected too.
    """

    def __init__(self, specs):
        self._specs = {}
        for spec in specs:
            if not isinstance(spec, SourceSpec):
                raise AdapterError("not_a_source_spec")
            if spec.category not in CATEGORIES:
                raise AdapterError("spec_unknown_category:" + str(spec.category))
            if spec.provenance not in PROVENANCE_CLASSES:
                raise AdapterError("spec_unknown_provenance:" + str(spec.provenance))
            if not isinstance(spec.freshness_s, int) or spec.freshness_s <= 0:
                raise AdapterError("spec_bad_freshness:" + spec.source_id)
            if spec.source_id in self._specs:
                raise AdapterError("duplicate_source_id:" + spec.source_id)
            self._specs[spec.source_id] = spec

    def get(self, source_id):
        return self._specs.get(source_id)

    def spec_freshness_ok(self, spec):
        """A source promising more than its category budget is mis-declared."""
        return spec.freshness_s <= FRESHNESS_BUDGET_S[spec.category]

    def coverage(self):
        out = {}
        for spec in self._specs.values():
            slot = out.setdefault(spec.category, {"sources": 0,
                                                  "valid_specs": 0})
            slot["sources"] += 1
            if self.spec_freshness_ok(spec):
                slot["valid_specs"] += 1
        for category in CATEGORIES:
            out.setdefault(category, {"sources": 0, "valid_specs": 0})
        return out

    def validate_all(self):
        bad = [spec.source_id for spec in self._specs.values()
               if not self.spec_freshness_ok(spec)]
        return bad


def canonical_order(records):
    """Order every incoming record BEFORE any decision is taken.

    Stage-6 correction: select() used to decide duplicates and category caps on
    arrival order, so shuffling the input changed which story won. The pipeline
    is now: validate -> canonical sort -> decide. The sort key is total and
    stable, ending in the content digest so two records agreeing on everything
    else still order the same way every run.
    """
    return sorted(records, key=lambda r: (
        CATEGORIES.index(r.category) if r.category in CATEGORIES else len(CATEGORIES),
        int(r.published_ms),
        str(r.source_id),
        str(r.content_sha256)))


def select(records, *, now_ms, registry, max_age_s=None, per_category_cap=None,
           bucket_ms=3_600_000):
    """Fail-closed selection over a canonical order.

    Every record is validated against the registry first; an invalid record is
    recorded with its reason and never reaches the decision stage. Decisions are
    taken on the canonical order, so the result is independent of arrival order.
    """
    if not isinstance(registry, SourceRegistry):
        raise AdapterError("registry_required")

    valid, invalid = [], []
    for rec in records:
        try:
            if not isinstance(rec, SourceRecord):
                raise AdapterError("not_a_source_record")
            spec = registry.get(rec.source_id)
            if spec is None:
                raise AdapterError("unknown_source:" + str(rec.source_id))
            if not registry.spec_freshness_ok(spec):
                raise AdapterError("misdeclared_spec:" + spec.source_id)
            validate_record(rec, spec=spec, now_ms=now_ms)
            valid.append(rec)
        except AdapterError as exc:
            invalid.append({"reason": str(exc),
                            "content_sha256": getattr(rec, "content_sha256", None),
                            "source_id": getattr(rec, "source_id", None)})

    ordered = canonical_order(valid)
    accepted, rejected, seen = [], [], set()
    counts = {}
    for rec in ordered:
        budget = FRESHNESS_BUDGET_S[rec.category]
        age_s = (now_ms - rec.published_ms) / 1000.0
        if age_s < 0:
            rejected.append({"reason": "published_in_future",
                             "content_sha256": rec.content_sha256,
                             "source_id": rec.source_id})
            continue
        if age_s > budget or (max_age_s is not None and age_s > max_age_s):
            rejected.append({"reason": "stale",
                             "content_sha256": rec.content_sha256,
                             "source_id": rec.source_id})
            continue
        key = dedupe_key(rec)
        if key in seen:
            rejected.append({"reason": "duplicate",
                             "content_sha256": rec.content_sha256,
                             "source_id": rec.source_id})
            continue
        if per_category_cap and counts.get(rec.category, 0) >= per_category_cap:
            rejected.append({"reason": "category_cap",
                             "content_sha256": rec.content_sha256,
                             "source_id": rec.source_id})
            continue
        seen.add(key)
        counts[rec.category] = counts.get(rec.category, 0) + 1
        accepted.append(rec)

    return {"accepted": accepted, "rejected": rejected, "invalid": invalid,
            "category_counts": dict(sorted(counts.items()))}


def coverage_timeline(records, *, bucket_ms=3_600_000, min_uptime=0.5,
                      expected=None):
    """Per-category availability per time bucket. Missing is NOT zero.

    A category with no record in a bucket is reported `missing`, never as a
    quiet market. A category below `min_uptime` in a block is excluded from that
    block with the reason recorded, so a dead feed cannot look like a calm one.
    """
    buckets = {}
    for rec in records:
        if rec.category not in CATEGORIES:
            continue
        b = int(rec.published_ms // bucket_ms)
        buckets.setdefault(b, {}).setdefault(rec.category, set()).add(
            rec.source_id)
    timeline = []
    for b in sorted(buckets):
        row = {"bucket_start_ms": b * bucket_ms, "categories": {}}
        for category in CATEGORIES:
            present = buckets[b].get(category)
            if not present:
                row["categories"][category] = {"status": "missing", "sources": 0}
                continue
            declared = (expected or _REGISTRY_CATEGORY_SOURCES).get(category, ())
            total = len(declared)
            ratio = (len(present) / total) if total else 1.0
            # `min_uptime` is the floor of acceptable coverage. Exactly at the
            # floor is degraded, not fine: one of two expected feeds is a gap,
            # and rounding it up would hide a half-dead category.
            status = "ok" if ratio > min_uptime else "degraded"
            row["categories"][category] = {"status": status,
                                           "sources": len(present),
                                           "uptime": round(ratio, 3)}
        timeline.append(row)
    return timeline


_REGISTRY_CATEGORY_SOURCES = {}


def register_category_sources(registry):
    """Record how many declared sources each category has, for uptime maths."""
    global _REGISTRY_CATEGORY_SOURCES
    out = {}
    for spec in registry._specs.values():
        out.setdefault(spec.category, []).append(spec.source_id)
    _REGISTRY_CATEGORY_SOURCES = out
    return out

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
    """Adapter contract. Every rule is a refusal, never a silent repair.

    IMPORTANT SCOPE NOTE (stage 7): `content_sha256` is an *integrity
    checksum* over `title|published_ms`. It proves the record was not corrupted
    between the adapter and the ledger. It proves NOTHING about who published
    it: a forged record can carry a perfectly valid checksum. Authenticity
    requires a signed or allowlisted origin and a digest over the raw payload,
    neither of which exists yet - see NEWS_V24_AMENDMENT_2.md.
    """
    if not isinstance(rec, SourceRecord):
        raise AdapterError("not_a_source_record")
    _strict_int(now_ms, "now_ms")
    if not isinstance(spec, SourceSpec):
        raise AdapterError("not_a_source_spec")
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
    budget_s = effective_freshness(spec)
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
    if not isinstance(rec.title, str) or not rec.title.strip():
        raise AdapterError("empty_title")
    canonical_url(rec.origin_url)
    if now_ms < rec.published_ms:
        raise AdapterError("decision_before_publication")
    if not isinstance(rec.content_sha256, str) or len(rec.content_sha256) != 64:
        raise AdapterError("digest_malformed")
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
            if (isinstance(spec.freshness_s, bool)
                    or not isinstance(spec.freshness_s, int)
                    or spec.freshness_s <= 0):
                raise AdapterError("spec_bad_freshness:" + str(spec.source_id))
            if not isinstance(spec.source_id, str) or not spec.source_id.strip():
                raise AdapterError("spec_bad_source_id")
            if spec.source_id in self._specs:
                raise AdapterError("duplicate_source_id:" + spec.source_id)
            self._specs[spec.source_id] = spec

    def get(self, source_id):
        return self._specs.get(source_id)

    def spec_freshness_ok(self, spec):
        """A source promising more than its category budget is mis-declared."""
        return spec.freshness_s <= FRESHNESS_BUDGET_S[spec.category]

    def category_sources(self):
        """Immutable view: category -> tuple of declared source ids."""
        out = {c: [] for c in CATEGORIES}
        for spec in self._specs.values():
            out.setdefault(spec.category, []).append(spec.source_id)
        return {c: tuple(sorted(ids)) for c, ids in out.items()}

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
        str(r.content_sha256),
        str(getattr(r, "origin_url", "") or "")))


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


def _strict_int(value, name):
    """Integer, and explicitly not a bool: `True` is an int in Python."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError("not_an_integer:" + name)
    return value


def canonical_url(url):
    """Canonical, credential-free http(s) URL, or a refusal.

    This is a *shape* check, not a trust check: a canonical URL proves nothing
    about who published it. See NOTES on provenance below.
    """
    if not isinstance(url, str) or not url:
        raise AdapterError("url_missing")
    if url != url.strip() or "\n" in url or "\r" in url or " " in url:
        raise AdapterError("url_has_whitespace")
    if "@" in url.split("://", 1)[-1].split("/")[0]:
        raise AdapterError("url_has_credentials")
    if not (url.startswith("https://") or url.startswith("http://")):
        raise AdapterError("url_bad_scheme")
    host = url.split("://", 1)[1].split("/")[0].split("?")[0]
    if not host or host.startswith(".") or ".." in host or "\\" in host:
        raise AdapterError("url_bad_host")
    lowered = url.lower()
    for marker in ("%00", "javascript:", "data:"):
        if marker in lowered:
            raise AdapterError("url_forbidden_marker")
    return url


def effective_freshness(spec):
    """The tighter of the category budget and the source's own promise.

    Stage-7 correction: a source promising 30 s inside a 900 s category was
    accepted with a 100 s lag, because only the category budget was consulted.
    A source's own promise is a contract, so it can only tighten the limit.
    """
    return min(FRESHNESS_BUDGET_S[spec.category], spec.freshness_s)


def coverage_grid(start_ms, end_ms, *, bucket_ms):
    """Every bucket in a frozen window, including empty ones (stage-7 P0-A).

    The old implementation iterated only the buckets that contained a record,
    so a total outage produced no buckets at all and the documented
    "missing != quiet" rule was unreachable.
    """
    start_ms = _strict_int(start_ms, "start_ms")
    end_ms = _strict_int(end_ms, "end_ms")
    bucket_ms = _strict_int(bucket_ms, "bucket_ms")
    if bucket_ms <= 0:
        raise AdapterError("bad_bucket")
    if end_ms <= start_ms:
        raise AdapterError("bad_window")
    first = start_ms // bucket_ms * bucket_ms
    out = []
    cursor = first
    while cursor < end_ms:
        out.append(cursor)
        cursor += bucket_ms
    return out


def coverage_timeline(records, *, start_ms, end_ms, bucket_ms=3_600_000,
                      registry, heartbeat_s=None, min_uptime=0.5):
    """Per-category availability over a FROZEN grid of buckets.

    Three states per category per bucket, and they are not interchangeable:

      ok        - source heartbeat present and coverage above the floor
      degraded  - heartbeat present but coverage at or below the floor
      missing   - a declared source reported nothing (a real gap)
      unknown   - no heartbeat information at all: we cannot tell a quiet
                  source from a dead one, so we refuse to guess

    A single story in a bucket is NOT proof that the source is alive; that is
    what `heartbeat_s` is for. Without heartbeats the honest answer is UNKNOWN,
    never "ok".
    """
    if not isinstance(registry, SourceRegistry):
        raise AdapterError("registry_required")
    # min_uptime is a ratio, so it is a float by nature; what must be excluded
    # is a boolean or a non-numeric type, not a fractional value.
    if isinstance(min_uptime, bool) or not isinstance(min_uptime, (int, float)):
        raise AdapterError("bad_min_uptime_type")
    if not (0.0 <= float(min_uptime) <= 1.0):
        raise AdapterError("bad_min_uptime")
    if heartbeat_s is not None:
        _strict_int(heartbeat_s, "heartbeat_s")
        if heartbeat_s <= 0:
            raise AdapterError("bad_heartbeat")

    declared = registry.category_sources()
    undeclared = [c for c in CATEGORIES if not declared.get(c)]
    if undeclared:
        # A category with no declared source cannot be measured, and an empty
        # denominator used to read as "ok"; that is exactly the false comfort
        # this stage removes.
        raise AdapterError("category_without_declared_sources:"
                           + ",".join(undeclared))

    buckets = {}
    for rec in records:
        if not isinstance(rec, SourceRecord):
            raise AdapterError("not_a_source_record")
        if rec.category not in declared:
            raise AdapterError("undeclared_category:" + str(rec.category))
        b = int(rec.published_ms) // bucket_ms * bucket_ms
        buckets.setdefault(b, {}).setdefault(rec.category, set()).add(
            rec.source_id)

    timeline = []
    for bucket_start in coverage_grid(start_ms, end_ms, bucket_ms=bucket_ms):
        row = {"bucket_start_ms": bucket_start, "categories": {}}
        present = buckets.get(bucket_start, {})
        for category in CATEGORIES:
            sources = present.get(category, set())
            total = len(declared[category])
            ratio = len(sources) / total if total else 0.0
            if heartbeat_s is None:
                status = "unknown"
            elif not sources:
                status = "missing"
            else:
                status = "ok" if ratio > min_uptime else "degraded"
            row["categories"][category] = {
                "status": status,
                "sources": len(sources),
                "declared": total,
                "uptime": round(ratio, 3)}
        timeline.append(row)
    return timeline


def dedupe_metrics(records, *, by_expected=None):
    """False merges and false splits of the title-based dedupe key.

    The key is deliberately crude (first ~80 normalised characters). This
    measures how crude, using only labels the caller supplies - never a model.
    """
    groups = {}
    for rec in records:
        groups.setdefault(dedupe_key(rec), []).append(rec)
    false_merges = 0
    for key, items in groups.items():
        if by_expected is not None:
            expected = {by_expected.get(r.content_sha256) for r in items}
            expected.discard(None)
            if len(expected) > 1:
                false_merges += 1
        elif len({r.origin_url for r in items}) > 1 and \
                len({r.published_ms for r in items}) > 1:
            false_merges += 1
    expected_groups = {}
    if by_expected is not None:
        for rec in records:
            label = by_expected.get(rec.content_sha256)
            if label is not None:
                expected_groups.setdefault(label, set()).add(
                    dedupe_key(rec))
    false_splits = sum(1 for keys in expected_groups.values() if len(keys) > 1)
    return {"records": len(records), "keys": len(groups),
            "false_merges": false_merges, "false_splits": false_splits,
            "note": "title-key dedupe is intentionally non-semantic"}

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
    # Stage 9: a probe schedule is declared per source, not inferred from the
    # data. `start_ms` is the registered epoch the schedule is anchored to and
    # `cadence_ms` is how often a probe is owed. Both optional: without them the
    # source's availability is UNKNOWN, never "probably fine".
    start_ms: int = None
    cadence_ms: int = None


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
    # The future-observation guard runs once, above, and keeps its original
    # name so existing callers and tests still see `observed_after_decision`.
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
            for name in ("start_ms", "cadence_ms"):
                value = getattr(spec, name, None)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, int):
                    raise AdapterError("spec_bad_" + name)
                if name == "cadence_ms" and value <= 0:
                    raise AdapterError("spec_bad_cadence")
            if (spec.cadence_ms is None) != (spec.start_ms is None):
                # Half a schedule is worse than none: it looks measurable.
                raise AdapterError("spec_half_schedule:" + spec.source_id)
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
    accepted, rejected, seen = [], [], {}
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
            # Stage-9 correction: the winning record is now named. Without it a
            # duplicate is indistinguishable from a silent drop, and an
            # auditor cannot tell WHICH story survived.
            rejected.append({"reason": "duplicate",
                             "content_sha256": rec.content_sha256,
                             "source_id": rec.source_id,
                             "dedupe_key": key[:64],
                             "deduplicated_by": seen[key]})
            continue
        if per_category_cap and counts.get(rec.category, 0) >= per_category_cap:
            rejected.append({"reason": "category_cap",
                             "content_sha256": rec.content_sha256,
                             "source_id": rec.source_id})
            continue
        seen[key] = rec.content_sha256
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


@dataclass(frozen=True)
class Heartbeat:
    """One availability probe result for one source.

    Exists because a story is NOT a heartbeat (stage-8 P0). The previous code
    flipped status to 'ok' whenever a story appeared, which is a liveness claim
    with nothing behind it. This record is the only thing allowed to make one.
    """
    source_id: str
    observed_ms: int
    ok: bool
    clock: str = "probe"


HEARTBEAT_STATES = ("healthy", "failed", "missing", "unknown")


def dedupe_metrics(records, *, by_expected=None):
    """False merges and false splits of the title-based dedupe key.

    Stage-8 correction: the number is only meaningful against GOLD LABELS the
    caller supplies. The earlier URL+timestamp heuristic could report a "false
    merge" for two genuine editions of the same story, which is a guess dressed
    as ground truth. Without labels the metric is not computed at all, and the
    report says so instead of inventing a number.
    """
    if by_expected is None:
        return {"records": len(records), "computed": False,
                "reason": "gold_labels_required",
                "note": "title-key dedupe is intentionally non-semantic"}
    groups = {}
    for rec in records:
        groups.setdefault(dedupe_key(rec), []).append(rec)

    false_merges = 0
    for _key, items in groups.items():
        labels = {by_expected.get(r.content_sha256) for r in items}
        labels.discard(None)
        if len(labels) > 1:
            false_merges += 1

    expected_groups = {}
    for rec in records:
        label = by_expected.get(rec.content_sha256)
        if label is None:
            continue
        expected_groups.setdefault(label, set()).add(dedupe_key(rec))
    false_splits = sum(1 for keys in expected_groups.values() if len(keys) > 1)
    return {"records": len(records), "keys": len(groups), "computed": True,
            "labeled": len([r for r in records
                            if r.content_sha256 in by_expected]),
            "false_merges": false_merges, "false_splits": false_splits,
            "note": "title-key dedupe is intentionally non-semantic"}


def coverage_grid(start_ms, end_ms, *, bucket_ms):
    """Every bucket in a frozen window, including empty ones."""
    start_ms = _strict_int(start_ms, "start_ms")
    end_ms = _strict_int(end_ms, "end_ms")
    bucket_ms = _strict_int(bucket_ms, "bucket_ms")
    if bucket_ms <= 0:
        raise AdapterError("bad_bucket")
    if end_ms <= start_ms:
        raise AdapterError("bad_window")
    out, cursor = [], start_ms // bucket_ms * bucket_ms
    while cursor < end_ms:
        out.append(cursor)
        cursor += bucket_ms
    return out


def validate_heartbeat(hb, *, registry, cutoff_ms):
    """Probe evidence must be well-formed, registered and not from the future."""
    if not isinstance(hb, Heartbeat):
        raise AdapterError("not_a_heartbeat")
    if not isinstance(hb.source_id, str) or not hb.source_id:
        raise AdapterError("heartbeat_bad_source_id")
    spec = registry.get(hb.source_id)
    if spec is None:
        raise AdapterError("heartbeat_unknown_source:" + hb.source_id)
    _strict_int(hb.observed_ms, "heartbeat_observed_ms")
    if hb.observed_ms > cutoff_ms:
        raise AdapterError("heartbeat_after_cutoff")
    if not isinstance(hb.ok, bool):
        # A truthy string is not a probe result; guessing here would fabricate
        # exactly the evidence this record exists to prove.
        raise AdapterError("heartbeat_result_not_bool")
    return spec


HEALTHY, INTERMITTENT, FAILED, MISSING, UNKNOWN, STALE = (
    "healthy", "intermittent", "failed", "missing", "unknown", "stale")


def expected_probes(spec, bucket_start, bucket_end, *, grace_ms):
    """The probe schedule a source OWNS inside one bucket.

    Stage-9 correction: "one successful probe and gaps <= cadence" declared an
    hourly bucket healthy from a single probe. The schedule is now explicit: a
    source registered with `start_ms` owes a probe every `cadence_ms`, and the
    expected instants inside the bucket are enumerated before any evidence is
    looked at. Grace extends the deadline of each probe; it never adds or
    removes an expectation.
    """
    schedule = getattr(spec, "start_ms", None)
    cadence = getattr(spec, "cadence_ms", None)
    if cadence is None or schedule is None:
        return None                     # no declared expectation -> unknown
    _strict_int(cadence, "cadence_ms")
    _strict_int(grace_ms, "grace_ms")
    if cadence <= 0 or grace_ms < 0:
        raise AdapterError("bad_cadence")
    first = schedule if schedule > bucket_start else bucket_start
    offset = (first - schedule) // cadence
    instant = schedule + offset * cadence
    while instant < schedule:
        instant += cadence
    expected = []
    while instant < bucket_end:
        if instant + grace_ms > bucket_start or instant >= bucket_start:
            if instant >= bucket_start:
                expected.append(instant)
        instant += cadence
    return expected


def heartbeat_state(probes, *, spec, bucket_start, bucket_end, grace_ms):
    """Availability of one source in one bucket, judged against its schedule.

    States are ordered by what they permit us to claim:
      unknown      - the source declares no schedule; nothing can be asserted
      missing      - a schedule exists but not one probe arrived
      failed       - probes arrived, none succeeded
      intermittent - some expected instances answered, others did not
      stale        - a probe answered, but the last expected instance before
                     the bucket end is unanswered: the feed may have died
                     silently
      healthy      - every expected instance is answered (or still inside grace)
    """
    expected = expected_probes(spec, bucket_start, bucket_end,
                               grace_ms=grace_ms)
    if expected is None:
        return UNKNOWN
    # Sort by the probe instant, not by the dataclass: Heartbeat is not orderable.
    inside = sorted((p for p in probes
                     if bucket_start <= p.observed_ms < bucket_end),
                    key=lambda p: p.observed_ms)
    if not expected:
        # No probe was due inside this bucket at all.
        return UNKNOWN if not inside else HEALTHY
    if not inside:
        return MISSING
    answered = 0
    for instant in expected:
        if any(abs(p.observed_ms - instant) <= grace_ms for p in inside):
            answered += 1
    if answered == 0:
        return FAILED
    if not any(p.ok for p in inside):
        return FAILED
    # Order matters: a feed whose last expected probe is unanswered may have
    # died silently, so `stale` outranks a partial answer set. A feed that
    # answered everything but the final instant is stale, not intermittent;
    # intermittent means it missed instances in the MIDDLE while still being
    # alive at the end.
    last = expected[-1]
    last_answered = any(abs(p.observed_ms - last) <= grace_ms for p in inside)
    if not last_answered:
        return STALE
    return HEALTHY if answered == len(expected) else INTERMITTENT


def coverage_timeline(records, *, start_ms, end_ms, bucket_ms=3_600_000,
                      registry, heartbeats=None, cutoff_ms=None,
                      grace_ms=90_000, min_uptime=0.5):
    """Per bucket: STORY coverage and AVAILABILITY, as independent reports.

    Availability comes only from heartbeat probes judged against each source's
    DECLARED schedule (start_ms + cadence_ms + grace). A source that never
    declares a schedule contributes `unknown`, never a health claim.

    A late probe can be supplied only through an explicit correction event
    (see `apply_correction`); it never mutates a frozen bucket silently.
    """
    if not isinstance(registry, SourceRegistry):
        raise AdapterError("registry_required")
    if isinstance(min_uptime, bool) or not isinstance(min_uptime, (int, float)):
        raise AdapterError("bad_min_uptime_type")
    if not (0.0 <= float(min_uptime) <= 1.0):
        raise AdapterError("bad_min_uptime")
    _strict_int(grace_ms, "grace_ms")
    if grace_ms < 0:
        raise AdapterError("bad_grace")

    declared = registry.category_sources()
    undeclared = [c for c in CATEGORIES if not declared.get(c)]
    if undeclared:
        raise AdapterError("category_without_declared_sources:"
                           + ",".join(undeclared))

    cutoff = (cutoff_ms if cutoff_ms is not None
              else _strict_int(end_ms, "end_ms"))
    _strict_int(cutoff, "cutoff_ms")

    quarantined = []
    ingested = {}
    for rec in records:
        try:
            if not isinstance(rec, SourceRecord):
                raise AdapterError("not_a_source_record")
            spec = registry.get(rec.source_id)
            if spec is None:
                raise AdapterError("unknown_source:" + str(rec.source_id))
            if rec.observed_ms > cutoff:
                raise AdapterError("ingested_after_cutoff")
            if rec.category not in declared:
                raise AdapterError("undeclared_category:" + str(rec.category))
            if rec.category != spec.category:
                raise AdapterError("category_mismatch:" + rec.source_id)
            b = int(rec.observed_ms) // bucket_ms * bucket_ms
            ingested.setdefault(b, {}).setdefault(rec.category, set()).add(
                rec.source_id)
        except AdapterError as exc:
            quarantined.append({"reason": str(exc),
                                "source_id": getattr(rec, "source_id", None),
                                "content_sha256": getattr(rec, "content_sha256",
                                                          None)})

    probes_by_source = {}
    quarantined_beats = []
    seen_beats = set()
    for hb in heartbeats or ():
        try:
            validate_heartbeat(hb, registry=registry, cutoff_ms=cutoff)
            key = (hb.source_id, hb.observed_ms, hb.ok, hb.clock)
            if key in seen_beats:
                # Duplicate probe: counted once. It is evidence, not a vote.
                continue
            seen_beats.add(key)
            probes_by_source.setdefault(hb.source_id, []).append(hb)
        except AdapterError as exc:
            quarantined_beats.append({"reason": str(exc),
                                      "source_id": getattr(hb, "source_id",
                                                           None)})

    timeline = []
    for bucket_start in coverage_grid(start_ms, end_ms, bucket_ms=bucket_ms):
        bucket_end = bucket_start + bucket_ms
        row = {"bucket_start_ms": bucket_start, "bucket_end_ms": bucket_end,
               "categories": {}}
        for category in CATEGORIES:
            sources = declared[category]
            seen = ingested.get(bucket_start, {}).get(category, set())
            story_ratio = len(seen) / len(sources) if sources else 0.0
            per_source, counts = {}, {}
            for sid in sources:
                spec = registry.get(sid)
                state = heartbeat_state(probes_by_source.get(sid, ()),
                                        spec=spec, bucket_start=bucket_start,
                                        bucket_end=bucket_end,
                                        grace_ms=grace_ms)
                per_source[sid] = state
                counts[state] = counts.get(state, 0) + 1
            healthy = counts.get(HEALTHY, 0)
            if counts.get(UNKNOWN, 0) == len(sources):
                status = UNKNOWN
            elif healthy:
                status = HEALTHY
            elif counts.get(FAILED):
                status = FAILED
            elif counts.get(STALE):
                status = STALE
            elif counts.get(INTERMITTENT):
                status = INTERMITTENT
            else:
                status = MISSING
            availability = (healthy / len(sources)) if sources else 0.0
            row["categories"][category] = {
                "story": {"sources_with_story": len(seen),
                          "declared": len(sources),
                          "coverage": round(min(story_ratio, 1.0), 3),
                          "status": ("ok" if story_ratio > min_uptime
                                     else ("none" if not seen else "partial"))},
                "availability": {"status": status, "counts": counts,
                                 "declared": len(sources),
                                 "uptime": round(min(availability, 1.0), 3),
                                 "per_source": per_source}}
        timeline.append(row)
    return {"timeline": timeline, "quarantined": quarantined,
            "quarantined_heartbeats": quarantined_beats,
            "note": ("availability comes only from scheduled probes; a late "
                     "probe changes a frozen bucket only via a correction")}


def apply_correction(timeline_row, *, source_id, late_probe, reason):
    """Explicit, versioned correction of one frozen bucket.

    A late heartbeat must not silently rewrite a decision that was already
    published. The corrected row is a NEW object with a version and the original
    state preserved, so the audit trail keeps both.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise AdapterError("correction_reason_required")
    if not isinstance(late_probe, Heartbeat):
        raise AdapterError("not_a_heartbeat")
    original = timeline_row["categories"][source_id]["availability"]
    corrected = json.loads(json.dumps(timeline_row))
    corrected["correction"] = {
        "source_id": source_id, "late_probe_observed_ms":
            int(late_probe.observed_ms), "late_probe_ok": bool(late_probe.ok),
        "reason": reason, "original_status": original["status"],
        "original_uptime": original["uptime"], "version":
            (timeline_row.get("version") or 1) + 1}
    corrected["version"] = corrected["correction"]["version"]
    return corrected

# NEWS v2.4 — pre-data amendment #2 (coverage grid, freshness, integrity vs provenance)

**Dated:** 2026-11-17 (UTC). **No v2.4 data exists.** Amends the v2.4 protocol;
touches nothing in v2.1 or v2.2.

## A2.1 — Coverage needs a frozen grid, not the records

The previous implementation built buckets **from the records**, so a total
outage produced no buckets at all: the documented "missing ≠ quiet" rule was
unreachable exactly when it mattered. Reproduced: `coverage_timeline([]) → []`.

Corrected: the caller freezes `start_ms`/`end_ms`/`bucket_ms`, and every bucket
in that window is emitted with all seven categories present.

## A2.2 — Item count is not liveness

One story in a bucket proves a source produced a story, not that it is alive.
Statuses are now:

| status | meaning | condition |
|---|---|---|
| `ok` | feed alive and coverage above the floor | heartbeat configured, sources > floor |
| `degraded` | alive but at/below the floor | heartbeat configured, ratio ≤ `min_uptime` |
| `missing` | specified source produced nothing | heartbeat configured, zero sources |
| `unknown` | no heartbeat information | `heartbeat_s is None` |

Without a heartbeat the honest answer is **unknown**, never `ok`. With exactly
50 % coverage the status is **degraded**: one live feed out of two is a gap.

## A2.3 — No shared global state

`register_category_sources()` mutated a module-level dict, so two experiments
interleaved in one process could inherit each other's declared sources, and an
empty denominator fell back to `ratio = 1.0 → ok`. That is now removed: the
registry is passed explicitly to every call, `category_sources()` returns an
immutable view, and a category with no declared source is a **hard refusal**
(`category_without_declared_sources`), not a silent pass.

## A2.4 — A source promise can only tighten the budget

`validate_record` used the category budget alone, so a source promising 30 s
inside the 900 s sanctions category accepted a 100 s lag. Freshness is now
`min(category budget, spec.freshness_s)`, applied to **both** transport lag and
decision age. Additionally: `now_ms` must be an integer and not a bool, and a
decision instant before publication is refused rather than silently tolerated.

## A2.5 — Integrity checksum is NOT provenance

`content_sha256 = sha256(title | published_ms)` proves the record was not
corrupted between adapter and ledger. It proves **nothing** about who published
it: a forged record can carry a valid checksum, and the same title with
different content, or a swapped URL under the same publisher, both pass.

**What would be needed for actual provenance (not implemented):**
1. a signed or allowlisted origin (key pinning or domain allowlist verified out
   of band, not from the record itself);
2. a digest over the **raw payload bytes** as fetched, not over derived fields;
3. retention of that raw payload as immutable evidence.

Until those exist, every statement in this repository must say "integrity
checksum", never "authentication". The registry verifies a *claimed* source id
against a declared spec; it does not prove the item came from that source.

## A2.6 — URL safety is a shape check

`canonical_url()` refuses credentials in the authority, whitespace, null bytes,
non-http(s) schemes (`ftp`, `javascript`, `data`) and malformed hosts. This is
hygiene, not trust: a syntactically perfect URL may still be a spoof.

## A2.7 — Ordering and duplicate collisions

The canonical order now includes `origin_url` after `(category, published_ms,
source_id, content_sha256)`, so two records that tie on every earlier component
still order the same way on every run (tested by permuting the input). Where two
records are identical on the full key the selector treats them as the same
record; it does not pick one at random.

## A2.8 — The dedupe key is deliberately crude

`dedupe_key` is the first ~80 normalised title characters. It is **not**
semantic, and it will both merge unrelated stories with similar headlines and
split the same story under different phrasings. `dedupe_metrics()` quantifies
both against caller-supplied labels — never a model — and the number is reported
alongside any result. A semantic dedupe would require a model call inside
ingestion, which is both a leak vector and a dependence on the system under
test.

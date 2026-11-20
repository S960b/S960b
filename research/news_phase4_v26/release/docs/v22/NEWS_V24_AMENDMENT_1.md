# NEWS v2.4 — pre-data amendment #1 (selector semantics)

**Dated:** 2026-11-17 (UTC). **Status:** no v2.4 data exists. This amends the
v2.4 protocol document; it changes nothing about v2.1 or v2.2.

## A1.1 — Order of operations was wrong and is now fixed

The first implementation decided duplicates and category caps **while iterating
the input list**, then sorted the survivors. That made the winner of a duplicate
pair depend on arrival order, which contradicts the claim of deterministic
sampling. Reproduced with the same two records in both orders: different
accepted digest.

Corrected pipeline, binding:

```
validate (fail-closed, against the registry)
  → canonical_order (total, stable sort)
  → decide (dedupe, caps, age)
```

`canonical_order` sorts by `(category order, published_ms, source_id,
content_sha256)`. Every component is part of the key, so two records agreeing on
everything but the digest still order identically on every run.

## A1.2 — Validation is enforced at the boundary

`select()` previously accepted whatever it was handed. An unknown category
raised `KeyError`; a forged digest, a wrong provenance or a future observation
would have been accepted silently.

Now every record is validated against a `SourceRegistry` **before** any decision,
and each failure is recorded with a reason rather than crashing:
`unknown_category`, `unknown_source`, `provenance_mismatch`,
`content_digest_mismatch`, `observed_after_decision`,
`transport_lag_too_large`, `too_stale`, `misdeclared_spec`.

A record cannot self-declare provenance: a source id that is not in the registry
is rejected, and a record whose provenance disagrees with its spec is rejected.
The registry itself refuses duplicate ids, unknown categories, unknown
provenance and a freshness promise wider than the category budget.

## A1.3 — Two clocks, not one

Freshness now enforces **both**:

| name | formula | meaning |
|---|---|---|
| transport lag | `observed − published` | how late the wire was |
| decision age | `now − published` | how stale the story is when we decide |

Checking only one let a backfilled record (published hours ago, observed
minutes ago) look fresh, and a record observed long after publication slip past.
Additionally: `published ≤ observed ≤ decision_time`, all timestamps must be
integers and **not** booleans (Python's `bool` is an `int` subclass).

`decision_time` is frozen before processing and never re-read inside the
selector.

## A1.4 — Estimand: event time vs arrival time

The two are not the same quantity and must not be conflated:

- **Event-time estimand** — the effect conditioned on when the *news* happened.
  This is what the protocol intends to measure, because the horizon is defined
  from the entry quote, not from ingestion.
- **Arrival-time estimand** — the effect conditioned on when *we noticed*.
  Easier to compute, but it silently rewards fast polling and cannot be
  compared across sources with different lags.

**Binding choice:** the primary estimand is event-time. Arrival-time statistics
are reported as a secondary description and must never be substituted for the
primary contrast. Because transport lag varies by source, a category whose lag
distribution differs materially (more than 2× the median for its block) is
flagged in the coverage report.

## A1.5 — Coverage: missing is not zero

`coverage_timeline()` reports, per category per time bucket, one of:

- `missing` — no record at all (never rendered as a quiet market);
- `degraded` — coverage at or below the `min_uptime` floor (exactly 50 % counts
  as degraded: one live feed out of two is a gap, not a pass);
- `ok` — above the floor.

Uptime is computed against the **declared** source set for the category, which
must be passed explicitly rather than inferred from whatever arrived.

## A1.6 — Accounting

`accepted + rejected + invalid == input` is enforced by test. A record is in
exactly one bucket: invalid means it never reached the decision stage, rejected
means it was eligible in shape but lost on a rule.

## A1.7 — What this amendment does not do

- No statistical evidence is created; there is no data.
- No live feed, no network code, no model call.
- v2.1 and v2.2 records, prompts and counters are untouched.

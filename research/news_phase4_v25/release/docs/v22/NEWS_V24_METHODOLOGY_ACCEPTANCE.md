# NEWS v2.4 — offline methodology acceptance artefact

**Dated:** 2026-11-17 (UTC). **Scope:** offline fixtures only.
**No prospective v2.4 data exists.** Nothing here is evidence about markets.

## 1. What this artefact certifies

| claim | status |
|---|---|
| The seven-category adapter contract validates recorded payloads | **accepted, offline** |
| Selection is deterministic and independent of input order | **accepted, offline** |
| Story coverage and availability are computed independently | **accepted, offline** |
| Accounting closes: input = invalid + accepted + rejected | **accepted, offline** |
| Records seal as three-arm triples and label from candle evidence | **accepted, offline** |
| Source authenticity / provenance | **NOT established** |
| Availability measured from real probes | **NOT established** (synthetic probes) |
| Any predictive performance | **NOT established** (mock arms) |

## 2. Reconciliation from the last run

```
input 9 → invalid 5, rejected 0, accepted 4
accepted 4 → recorded 4 → labelled 4, unlabelled 0
export: 4 triples, 12 arms, 4 outcomes
balances: selection true, records true
```

The five refusals are three age failures, one duplicate and one digest
mismatch — every channel is populated, so the balance is meaningful rather than
accidentally true on an empty set.

## 3. Why "story" and "availability" are separate

A story proves a story. A heartbeat probe proves a feed. Conflating them
produced claims that could not be supported: "ok" was reached by seeing one news
item, and a category covering 100 % of its stories could be 100 % dead.

| availability | meaning |
|---|---|
| `healthy` | at least one successful probe, probe gaps within declared cadence |
| `failed` | probes exist, none succeeded in the bucket |
| `missing` | a probe was expected, none arrived |
| `unknown` | cadence not declared — expectation cannot be established |

A bucket with zero stories and a healthy feed is **healthy / none**, never
"missing". An empty bucket is not an outage.

## 4. Limitations recorded here so they cannot be quietly dropped

1. **`content_sha256` is an integrity checksum**, computed over
   `title|published_ms`. A forged record carries a valid one. Authenticity
   requires a signed or allowlisted origin and a digest over the raw payload,
   neither of which exists.
2. **`heartbeat_s`/cadence are parameters.** Until a real availability probe
   exists, most buckets report `unknown` — the correct answer, and it will look
   like very little coverage.
3. **The dedupe key is non-semantic.** False merges and splits are measured only
   against gold labels; without labels the metric reports `computed: false`
   rather than inventing a number.
4. **Mock arms are not a backtest.** The acceptance run proves plumbing, not
   signal.
5. **Selection remains non-random**, so the estimand is selection-conditioned
   (amendment #1, §A1.4).

## 5. GO / NO-GO

| item | verdict |
|---|---|
| Offline fixtures, selector, coverage, ledger, labeler | **GO** |
| Authentic source provenance | **NO-GO** |
| Online adapters | **NO-GO** — no network code exists, by design |
| Source licence review | **NO-GO** — not started |
| Live prospective deployment (v2.4) | **NO-GO** |
| Live v2.2 three-arm pilot | **NO-GO** — owner gates (stale GUI, overdue pilot, D16 migration, D14 duplicate) |

## 6. What a live v2.4 run would additionally require

1. A concrete, licensed source list with a dated freeze.
2. A real availability probe producing `Heartbeat` records on a declared cadence.
3. Signed or allowlisted origins, plus raw-payload retention and digests over it.
4. A separate counter and a separate export directory.
5. Written confirmation that v2.1 and v2.2 remain untouched.

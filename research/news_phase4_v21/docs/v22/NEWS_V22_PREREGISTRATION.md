# NEWS v2.2 — three-arm pre-registration amendment

**Dated amendment:** 2026-11-16 (UTC), written **before** any v2.2 data exists.
**Status:** protocol proposal. No data has been collected under this version.

**Naming (corrected 2026-11-17, before any data):** the version is **2.2** and
the stored protocol string is `prospective-v2.2-arms3`. An earlier draft of this
document wrote `prospective-v2.3-arms3` in one place and `v2.2` elsewhere; that
was an internal inconsistency, not a second protocol. `v22_arms.PROTOCOL_VERSION`
is the single source of truth, and a test pins the document to it. The
source-broadening protocol remains a separate, later **v2.4**.
**Relation to v2.1:** additive. v2.1 (`prospective-v1`, two arms N/NM) stays
frozen, its prompts and rows are never edited, and its six existing records keep
their meaning. Anything below applies only to records stamped
`prospective-v2.2-arms3`.

## 1. Why a third arm

v2.1 compares N (news only) against NM (news + market). That contrast cannot
answer the question the experiment exists for: *does the news block add
anything?* Both arms see news, so a difference can only be attributed to the
market block. The missing control is a market-only arm.

| arm | sees | answers |
|---|---|---|
| N — `news` | news block only | is news alone informative? |
| NM — `news_market` | news block + frozen market vector | does market help news? |
| M — `market` | frozen market vector only | what is achievable with **no news at all**? |

**M is built from the market dict alone.** It is constructed, not filtered from
the news payload, because filtering leaves a leak channel open every time a
field is added. `verify_market_prompt()` enforces this mechanically: no news
field name inside the frozen JSON, no event title, summary, source, or event id
anywhere in the prompt. A hit is a hard refusal, not a warning.

## 2. What is held identical across the three arms

| property | value | why |
|---|---|---|
| symbol | BTCUSDT | — |
| market vector | the same frozen dict object | a different vector would make NM−M measure the data, not the news |
| cutoff | the same `market_cutoff_ms` | features must be pre-decision |
| horizon | 4 h, identical for all arms | — |
| entry | a single quote sampled **after the last arm finishes** | otherwise the arms race against each other |
| exit | first closed 1 m candle at/after due, drift ∈ [0, 60000) | D15 evidence rules apply unchanged |
| costs | 2 bps fee + 1 bps slippage per side = 6 bps round trip | applied identically; FLAT pays nothing |
| prompt header | one header, differing only in the `ARM:` line | — |
| tool evidence | tri-state; only `known_zero` on all three arms may seal | D01–D03 |

## 3. Estimand and its bias (stated up front)

Arms are matched on the same event, so the primary estimand is the
**paired, selection-conditioned** difference in proper score:

    Δ(arm_a, arm_b) = E[ S(arm_a) − S(arm_b) | event selected ]

Selection is shared across arms and is **not** randomised. The selector prefers
`fast_verified`, primary sources, higher relevance and younger events. This
means:
- the estimand is conditional on the selector and is **not** the population
  effect on all news;
- any selector-side advantage applies equally to N, NM and M, so the
  **difference** between arms stays interpretable even though each arm's
  absolute score does not;
- the M arm never sees the fields the selector filters on. This is unavoidable:
  a matched design requires the same event. It is disclosed, not hidden.

## 4. Hypotheses

- **H1 (news is informative).** N beats M on the primary score.
- **H2 (market is informative).** M beats the zero-trade base rate.
- **H3 (combination).** NM beats both N and M.
- **H4 (calibration).** Each arm's predicted probabilities are calibrated:
  the Brier score is not worse than that of the constant base-rate predictor.
- **H5 (actionability).** The pre-declared action rule produces paper PnL
  that survives 6 bps round-trip costs and beats a zero-trade baseline.

**Incremental news value** is the contrast family NM − M and N − M. Both are
reported whether or not they are significant, with the block bootstrap CI.

## 5. Statistics

| element | decision |
|---|---|
| primary loss | **Brier score** (log loss reported in parallel, never mixed) |
| comparison | paired per event, same events in all arms |
| resampling | **block bootstrap by UTC day**, 2000 resamples, seed 20261116 |
| interval | 2.5/97.5 percentile of the resampled mean difference |
| multiplicity | 3 pre-declared contrasts (N−M, NM−M, NM−N); Holm–Bonferroni at α=0.05 family-wise; no post-hoc contrast may be called confirmatory |
| power | 0.80, α=0.05, paired normal approximation |
| MDE | ΔBrier ≈ 2.80·σ/√n; with a deliberately conservative σ=0.5 this is **1.40/√n** |
| n for MDE 0.02 | ≈ 4 900 events |
| n for MDE 0.05 | ≈ 785 events |
| stopping | fixed-horizon only. No peeking, no early stopping on a favourable CI. A futility stop is allowed **only** if the pre-declared MDE can no longer be reached inside the time budget, and that stop must be dated and justified in writing. |

**A null result is a valid, publishable outcome.** H1–H5 failing is not a bug
and does not trigger a redesign of the prompt or the selector.

## 6. Zero-trade baseline

An arm that always answers FLAT is a legitimate competitor. It scores against
the base rate and pays no costs. Any trading arm must beat it **after** costs;
matching it is a failure of the information set, not a success of the rule.

## 7. Action rule, frozen before data

| condition | action |
|---|---|
| p_up ≥ 0.55 and expected_return_bps ≥ +35 | LONG_PAPER |
| p_up ≤ 0.45 and expected_return_bps ≤ −35 | SHORT_PAPER |
| otherwise | FLAT |

These thresholds are the v2.1 values carried forward unchanged. **No tuning
against v2.2 data is permitted.** Any change requires a new version number and
a new dated amendment, and the changed version is analysed separately, never
pooled.

## 8. What would falsify the protocol

If, after n = 785 matched events, the M−zero baseline contrast is indistinguishable
from zero while N−M is also indistinguishable from zero, the honest conclusion
is that this data pipeline carries no measurable BTC 4 h signal, and the
experiment stops rather than growing.

## 9. Immutability

- Prompt digests are stored per arm per event (`prompt_sha256` in `v22_arms`).
- `seal_sha256` covers the three response hashes, the entry quote, the cutoff
  and the protocol version.
- Rows are insert-only. A correction is a new dated annotation, never an update.

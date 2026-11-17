# NEWS v2.2 — pre-data methodology amendment #2

**Dated:** 2026-11-17 (UTC). **Status:** written before any v2.2 data exists.
Amends `NEWS_V22_PREREGISTRATION.md`; it does not replace it. Where the two
disagree, this document wins and the original stays for the record.

## A2.1 — Prediction metric and action baseline were conflated

The first draft compared an arm's **Brier score** to "the zero-trade base rate".
That is a category error:

- a Brier score measures a **probability forecast**;
- a zero-trade rule is an **action policy** and has no probability at all.

Two separate references are now defined.

**(a) Prediction reference — climatology.** A constant forecast equal to the
outcome frequency. Brier and log loss of each arm are compared to it, and to
nothing else. Additionally, the climatology must be **fitted on prior windows
only**: `walk_forward_climatology()` produces a base rate per row using history
that precedes it, because a full-sample base rate contains the outcome it is
predicting and would flatter or punish a model for the wrong reason. Until two
prior rows exist, no comparison is reported for that row.

**(b) Action reference — FLAT.** A rule that always answers FLAT. It is compared
only in **PnL terms**, after 6 bps round-trip costs, and never in Brier terms.
An arm can beat FLAT on PnL while being a worse forecaster, and the protocol now
says so explicitly instead of hiding it in one number.

## A2.2 — Dependence structure of the sample

Events are not independent draws:

- two events in the same UTC day share a market regime;
- 4 h horizons can **overlap** when events are closer than 4 h apart;
- the selector's keyword gate makes inclusion non-random.

Consequences, binding:

1. The bootstrap resamples **whole UTC days**, never individual events.
2. Overlapping windows are a known defect, not corrected away: with horizons of
   4 h and events closer than that, two labels can share price action. The
   protocol requires reporting the fraction of events whose 4 h window overlaps
   an earlier event's window, and the primary contrast is additionally reported
   on the **non-overlapping subset** as a sensitivity check. If the two disagree
   in sign, the result is not confirmatory.
3. Selection conditioning is disclosed in the estimand (§3 of the original
   amendment) and is not removable by matching; the matched design makes arm
   *differences* interpretable, not absolute scores.

## A2.3 — Fixed-horizon sample size and the σ assumption

The MDE formula used σ = 0.5 as a deliberately conservative stand-in for the
per-event paired Brier difference. That number is an assumption, not a
measurement, and it must not be presented as if it were known.

Binding rules:
- sample size is fixed **before** data collection (fixed horizon);
- no peeking, no early stop on a favourable CI;
- the assumed σ is labelled as an assumption in every report that quotes an MDE;
- σ is **recomputed from observed data only for the next version's planning**,
  never to resize the current experiment after looking at results.

## A2.4 — Multiplicity

Three contrasts are declared (N−M, NM−M, NM−N) and Holm–Bonferroni at
family-wise α = 0.05 is applied to exactly those three. Any further contrast —
including any subgroup, any day-of-week slice, any source category — is
**exploratory** and may not be reported as confirmatory, however small its
p-value. Adding a fourth pre-declared contrast after seeing data is prohibited.

## A2.5 — Threshold freeze

The action thresholds (p_up ≥ 0.55 / ≤ 0.45 combined with |bps| ≥ 35) are frozen
from v2.1 and are **not** fitted to v2.2 data. A change of threshold creates a
new version and a new dated amendment; the two versions are analysed separately
and never pooled.

## A2.6 — Censoring, missingness and how a gap is reported

- A pair whose candle evidence fails (partial candle, drift ≥ 60000 ms, missing
  data) is **censored**, not silently dropped: it is counted, its reason is
  recorded, and the censoring rate is reported next to every result.
- A pair with a failed or unknown arm is never sealed, so it never enters the
  analyzable set; it appears in the attempt ledger with its terminal state.
- Missing outcomes are never imputed. An arm cannot be credited for a label that
  does not exist.
- If the censoring rate exceeds 10 %, the result is reported as **inconclusive
  by attrition** regardless of the point estimate.

## A2.7 — What this document does NOT do

- It does not create any statistical evidence. No v2.2 data exists.
- It does not turn synthetic fixtures into a backtest. Any run over mock arms is
  a plumbing test: it proves the code paths, never that an arm predicts.
- It does not change v2.1, its header, its six records or its estimand.

# NEWS Intelligence: Phase 4 v2 probabilistic forward-only paper pilot

Research/code: ChatGPT, 2026-11-09. Execution: Kali MultiTool agent. Strictly NO trading.

## Why v2

Phase 3 and Phase 4 v1 have mostly NEUTRAL forecasts, so no directional
accuracy or measurable trading edge. v2 asks for a probability and an expected
four-hour BTC spot return; an independent deterministic rule decides whether
a hypothetical trade clears a fixed fee and slippage threshold. All previous
experiments and databases remain intact. Neither model calls nor trading
are enabled by default.

## What we reused from prior work

- FinGPT: https://github.com/AI4Finance-Foundation/FinGPT — approach to
  financial news modelling and benchmarks, no pretrained weights installed.
- FinBERT: https://huggingface.co/ProsusAI/finbert — optional third-party
  financial sentiment ablation for later, NOT installed; weights have separate
  license restrictions.
- Triple barrier and meta labeling:
  https://random-docs.readthedocs.io/en/latest/implementations/tb_meta_labeling.html
  — separate prediction from trading/filtering decisions. Our first gate is
  deterministic, not a trained meta-classifier or a triple-barrier estimator.
- Binance official public spot API and history:
  https://developers.binance.me/en/docs/products/spot/faqs/market_data_only
  https://github.com/binance/binance-public-data
  — existing Phase4 collector is source of forward-sealed public evidence.
  Future historical research must account for Binance bulk Spot CSV timestamps
  expressed in microseconds from 2025 onward, unlike REST milliseconds.
- Freqtrade lookahead validation:
  https://www.freqtrade.io/en/stable/lookahead-analysis/
  — forbid future market candles in a prediction.
- Brier score proper scoring rule:
  https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html
  — implement small independent stdlib evaluator.
- Crucial counterevidence:
  https://link.springer.com/article/10.1186/s40537-026-01392-x
  — one 2026 study found ChatGPT sentiment did not significantly improve
  out-of-sample Bitcoin return forecasts.
  https://www.mdpi.com/2673-4591/143/1/31
  — another BTC daily XGBoost study found that news sentiment gave little
  incremental predictive value over technical features. These conclusions
  are not proofs about our prospective intraday paired study.
- Short-horizon macroeconomic-news reactions are sometimes conditioned on
  market sentiment, but association is NOT proof of trading profitability:
  https://doi.org/10.1016/j.jbef.2026.101183

## Locked cohort and hypothesis

Compare N (same news only) vs NM (exact same news plus closed pre-cutoff market
features) for a strictly prospective BTCUSDT 4h spot return. Both calls
use the same actually observed MultiTool provider/model and one common entry
quote sampled after BOTH model completions. The v2 cohort is independent.

Evidence is read-only from existing Phase4 sealed SQLite and SHA-256-verified
26 fully closed 1h market candles. We require event cutoff strictly after
v2 initialization; late delivery up to 30 minutes; never retroactive prompts.
One N/NM pair per 15-minute UTC sampling slot, at most 4 model calls per UTC
day and 80 attempts lifetime. All attempts persist before external effects.
Ambiguous SDK calls must never be replayed. Gateway calls are free to owner;
limits protect experiment quality and incident isolation, not billing.

STRICT model output JSON object:
  {"p_up":0.62,"expected_return_bps":70,
   "uncertainty":0.45,"rationale":"hypothesis"}

The model does NOT choose a trade. Numbers and their meanings:
- p_up: raw model estimate of the probability of strictly positive 4h
  return from the shared post-inference BTC spot quote.
- expected_return_bps: signed expected return in basis points (1 bp=0.01%).
- uncertainty: subjective model uncertainty 0..1. Uncalibrated until tested.
- rationale: short explanation, under 240 characters, never used as a signal.

## Predeclared deterministic decision policy

Version: p4v2-prob-cost35-20261109; frozen prompt hash stored in new SQLite DB.

Assumed roundtrip fee = 20 bps; slippage = 5 bps; additional buffer = 10 bps.
These are hypotheticals, not verified Binance fills. Fixed paper decision:
- LONG_PAPER if p_up >= 0.55 and expected_return_bps > +35.
- SHORT_PAPER if p_up <= 0.45 and expected_return_bps < -35.
- Otherwise FLAT, cash benchmark 0.
SHORT_PAPER is NOT directly executable on ordinary unleveraged Spot and
does not account for borrowing, margin, funding, stress spreads or liquidation.
We NEVER place real orders. Do not interpret short paper PnL as realizable.

Prediction target: 4h. Labels for 1h, 4h, 24h are captured later from fully
closed public Binance BTCUSDT 1m candles after horizon +120-second safety wait.
Only 4h is the primary predeclared forecast evaluation. No pseudo-backfill.

Primary comparisons: paired Brier loss NM-N (smaller better); paired
mean hypothetical net result NM-N (bigger better); model return MAE;
action/abstention coverage; constant p_up=0.5 Brier benchmark; cash and
gross HODL; adjacent overlapping 4h windows and conservative nonoverlap count.
Report low-n results as descriptive only, never as proven profitability.
Later calibration/training must use a separate time-ordered fold with
purging/embargo around overlapping event windows, then unseen holdout.

## Files

- v2_core.py: pure, strict probabilistic parsing, independent paper gate, metrics.
- v2_source.py: read-only sealed Phase4 v1 news + market evidence.
- v2_cycle.py: new v2 SQLite, two reserved attempts, SDK session/receipt,
  post-inference public quote and matured labels; CLI DEFAULT OFF.
- news-p4-v2-helpers.mjs: MultiTool native SDK bridge, no prompt replay.
- news-p4-v2-plugin.mjs: optional separate plugin, DEFAULT ALL OFF.
- test_v2_core.py, test_v2_cycle.py and test_news_p4_v2_helpers.mjs:
  fully synthetic, no model, no network, temp databases only.

## Offline tests (run in detached Git worktree)

cd packages/news_phase4_v2
python3 -m py_compile v2_core.py v2_source.py v2_cycle.py
python3 -m unittest discover -v -p 'test_v2_*.py'
node --test test_news_p4_v2_helpers.mjs

This run only checks code. It does NOT prove forecast skill.

## Stage A: safe model-off pilot on Kali

1. Checkout exact pinned Git SHA in temporary worktree. Run the tests above,
   inspect source and verify NO changes to Phase3, Phase4 v1, SQLite, plugin
   configuration or trading code.
2. Read-only status:
   python3 v2_cycle.py status --root /home/kali/.local/share/news-phase4-v2
   This must NOT create a database.
3. Only after tests pass, install v2 files separately at
   /home/kali/research/news-phase4-v2 with root mode 0700 and files 0600.
   Initialize fresh private v2 DB via:
   python3 v2_cycle.py init --enable-init --root /home/kali/.local/share/news-phase4-v2
   The timestamp enforces forward-only predictions.
4. Do NOT change MultiTool config or reload GUI at Stage A. Verify dry-run
   CLI and read-only source, evidence and disk/clock health.
5. If all gates pass, a separate, reviewable plugin STAGE B can be registered
   inert with dry_run=true, model_calls_enabled=false, model_auto_enabled=false,
   label_auto_enabled=false and daily_call_cap=0.
   GUI MultiTool has NO proven programmatic safe reload; do not kill/restart it.
6. For live paired paper-pilot STAGE C (after safe plugin activation), explicit
   opts are dry_run=false, model_calls_enabled=true, model_auto_enabled=true,
   label_auto_enabled=true, daily_call_cap=4. Run one genuine new forward N/NM
   pair, confirm identity, common quote and delayed labels. Do not force
   retrospective input; do not change Phase3/Phase4 v1 or any real trading.

## Known constraints

No prediction accuracy has yet been measured for v2, no training/calibration,
no FinBERT model installation, no claims of profits, no real Binance API keys,
no leverage or live trading. FinBERT/FinGPT, additional data sources and
sequential calibration are future optional ablations, not hidden dependencies.


## Post-activation addendum: incomplete-pair recovery (2026-11-09)

The first live pair completed N but failed during NM finalization. Two model
attempts were reserved and BOTH SDK prompts were sent, while only one decision
was durably recorded. There is no common entry quote or 4h paper outcome.
The original pair is NOT a complete observation. It must NOT be duplicated,
backfilled, treated as FLAT or assigned synthetic 4h labels.

Code for explicit recovery:
- v2_recovery.py adds a new append-only abandoned_pairs ledger with SQL
  triggers forbidding retroactive finalization or quotation of abandoned pairs.
- v2_cycle.py recovery-schema and abandon commands are separate, explicit
  human-approved operations. The original attempts, session IDs and the
  completed N decision remain immutable; no deletion or SDK replay occurs.
- v2_cycle.py prepare skips only truly abandoned pairs after migration;
  lifetime and UTC-day call quotas still count all reserved attempts.
- v2_milestones.py shows abandoned-pair counts, excludes them from *first
  valid pair* milestone and keeps a distinct waiting-after-abandonment state.
- Tests: test_v2_recovery.py and test_v2_milestones.py; no live network.

Before using the recovery CLI, a local executor must inspect the existing
ambiguous session and provider/model **metadata only**, and classify whether
the cause was a model result JSON shape, missing model identity or an
unexpected Python finalization error; do NOT re-issue prompt. The log
v2_python_failed does NOT alone establish which error occurred.

After source SHA is pinned and all Python/Node tests pass, the MT executor
must back up and verify the v2 DB using SQLite's online backup method,
then deploy v2_recovery.py first, v2_cycle.py and v2_milestones.py second
without changing plugins, stored prompts, budgets or v1 modules. Check there
is no active v2 inference (and no in-flight second arm).

Authorized two-step recovery on the SPECIFIC first failed event (full 64-char
id required, obtained from v2 DB, not from the 12-char report prefix):

    python3 v2_cycle.py recovery-schema --enable-recovery-migration \
      --root /home/kali/.local/share/news-phase4-v2
    python3 v2_cycle.py abandon --enable-abandon --event-id FULL_EVENT_ID \
      --root /home/kali/.local/share/news-phase4-v2

Only proceed if a fresh diagnostic confirms exactly two durable prompt marks,
exactly one decision, zero quotes, and zero labels; never assume a partial
report still reflects the live DB. An unchanged initial model/policy prompt
contract_sha is mandatory. If any unexpected state or test failure, STOP and
return to ChatGPT. Do NOT mark any other pair abandoned.

After successful recovery, new Phase4-v1 forward events, not the old one,
may produce v2 N/NM pairs. A second identical finalize failure should halt
further auto model pilot until a separate versioned fix to output handling is
reviewed, to avoid wasting model calls. NEVER modify the existing completed
N forecast or score the partial pair as if complete.


## Recovery patch update: actual first-pair RCA and narrow validator tolerance

The forensic RCA from the already-existing, read-only MultiTool SDK history
confirmed the missing NM response was NOT lost: it had valid signed probability
and expectation, correct model identity, normal completed task outcome, but
a rationale of 271 characters (31 over the 240-character prompt request).
The original strict `decode_prediction` rejected this with
`V2Error: invalid_rationale`. That explains the failure of the first live
paired attempt. Do NOT replay or belatedly quote this old event: it no longer
has a contemporaneous post-inference entry quote.

Operational safety patch (same pinned policy version and prompt hash):
- Continue requesting rationale <240 characters in the original prompt;
  allow up to **1024** characters in the validator, because rationale is
  ignored for decisioning and is never saved to the DB. The original full
  model response is hash-fingerprinted. Numeric fields, allowed schema,
  model identity, output-size cap, decision rules, fees and horizons
  are UNCHANGED. Over 1024 characters still fails closed.
- The regression test accepts the observed 271-character explanation,
  tests the 1024-character boundary and rejects 1025.
- The 15-minute time-slot lock remains enforced even after explicit
  abandonment; a recovery test checks same-slot skip and next-slot
  ability to look for a NEW, unused event only.
- Recovery remains manually authorized and append-only, not automatic.
  The expired first pair remains incomplete and must not enter statistics.

If the same class of nonfinancial formatting issue reoccurs outside the
new bounded acceptance, stop and preserve the original source. Do not
silently widen numerical bounds, relax evidence timing or replay prompts.


## Early first-arm failure: second manual recovery (2026-11-10)

A later prospective v2 pair event prefix `6fee9bb3e150` was reserved at
2026-11-10 04:04:59 MSK during a MultiTool GUI restart. Its first NEWS
session was bound and the prompt was marked attempted, but no assistant
message/decision was present in the read-only local session history. The
NEWS_MARKET arm never got a session. Exactly: (attempts=2, sessions=1,
prompts=1, decisions=0, quotes=0, outcomes=0). The restart overlap is a
plausible hypothesis, NOT a proven root cause. No model prompt may be replayed.

This event cannot use the old `abandon` command, whose shape is
(2 attempts, 2 sessions, 2 prompt marks, 1 decision). Rather than weakening
old SQL constraints or relabeling the error, ChatGPT authored a separate
`v2_early_recovery.py` append-only ledger for this **specific** new shape:

- `abandoned_early_pairs` is a new table; the old abandoned-pair table, old
  attempts, decisions, sessions, prompt marks, source hashes, labels and
  policy are preserved unchanged.
- SQL triggers forbid UPDATE/DELETE on this ledger, and forbid further
  session binding, prompt marks, decisions, entry quotes or attempts
  for an early-abandoned event. The main cycle checks too.
- Allowed shape is **only** (2 attempts, 1 NEWS session, 1 NEWS prompt,
  0 decisions, 0 quotes, 0 outcomes), after at least 30 minutes.
  Both operations require separate explicit CLI opt-ins. Other incomplete
  shapes and completed pairs are rejected, not guessed.
- `_pending` excludes either explicitly abandoned cohort while keeping
  both reserved attempts within the existing 4/day and 80/lifetime caps.
  The 15-minute slot remains consumed. A new pair needs a **NEW**
  Phase4 sealed event and a future slot.
- The monitor retains old `abandoned_pairs`, separately counts
  `abandoned_early_pairs` and reports `pending_pairs`; it does not
  fabricate outcomes from either failed pair.
- Regression tests use only synthetic temporary private SQLite.
  This does NOT change the frozen probability prompt or thresholds.

**Deployment rules:** MT must check a pinned source commit, run the complete
Python and Node test suites, verify exact live shape, no SDK/GUI cycle in
flight and backed-up v2 DB with `PRAGMA integrity_check`, then install
`v2_early_recovery.py` BEFORE replacing `v2_cycle.py` and
`v2_milestones.py`. Do NOT modify JS plugin, GUI config, Phase4-v1 or
Phase3. No GUI restart is needed because Python starts per cycle.

Explicit one-time recovery (ONLY if live state still matches and
tests are all green):

    python3 v2_cycle.py early-recovery-schema --enable-early-recovery-migration --root /home/kali/.local/share/news-phase4-v2
    python3 v2_cycle.py abandon-early --enable-abandon-early --event-id FULL_64_HEX_EVENT_ID --root /home/kali/.local/share/news-phase4-v2

Never substitute the 12-character public event prefix for the full event_id.
Inspect it from the private DB, not from the public report. Do NOT rerun
the original prompt or invent a quote.

If this class of early failure reoccurs, retain the pending gate, stop
before further manual abandonments, and report technical diagnostics to
ChatGPT to design a separate prevention fix. A model-only v1 pause MUST
keep its public news collector and delayed labeling active so v2 still
receives genuinely fresh sealed events. Beware of low remaining host disk
space (~4.5 GiB at the latest check); do not delete evidence opportunistically.

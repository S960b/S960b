# NEWS Intelligence — Phase 3 (paper-only BTC forward study)

**Status: kickoff / investigation in progress (2026-11-08).** Phase 2 is completed; Phase 3 is NOT yet installed, collecting live Phase 3 predictions, or profitable. This document is the implementation and evaluation contract for a separate Git dialogue, not an authorization to trade.

**Task:** `task_e6f8b71ffee2f3a332c2a4adc6f5be57` / branch `dialogs/task_e6f8b71ffee2f3a332c2a4adc6f5be57`.

## Why a new phase is needed

NEWS Phase 2 v1.3.1 has a durable, deliberately irreversible pilot gate: `auto_state()` stops after **one** prediction in SQLite; the plugin stops its auto timer after the first successful prediction. The first observation is already labeled for 1/4/24 h and must be preserved. Merely enabling `auto_until_first_prediction` cannot collect a representative sample. No mutation/reset of the Phase 2 production DB or its `model_attempt` evidence is allowed.

Historical Phase 2 observation, for context only:
- Entry 2026-11-06T18:26:24.039Z, BTC USD 75783.60, DOWN, confidence 0.62, one model attempt.
- At +24h BTC change +0.0361%; DOWN hypothetical net -0.3361% with a **0.30% round-trip** assumed charge. Not a real trade, not a statistical result.
- Phase 2 completion confirmed by MT: 1 forecast, 3 outcome labels, no pending batch, model idle; 54/54 local tests.
- **Accounting mismatch to resolve:** Phase 2 report describes a 0.19% round-trip in prose, but actual formula uses 0.003 = 0.30%. Phase 3 must pick and disclose fee assumptions explicitly, recompute comparisons consistently, and must not silently mix them.

## Target experiment and firm boundaries

- Research/paper mode only: **NO real trades/orders, no funds, no private Binance API, no withdrawals, no exchange account access**.
- Existing MT gateway/model session; **no new stand-alone DeepSeek API key**. Confirm actual gateway model/version before naming its outputs.
- Keep original Phase 2 DB and code backups immutable. Use a separate Phase 3 SQLite DB and run directory with restricted owner permissions. Data migration is not a way to reset budgets.
- Pilot target **30 confirmed independent forecast issuance events**, max **36 external model attempts** (to cap costs from failed parses). No retries of ambiguous model calls; the one-call-per-batch invariant remains. A 4h minimum forecast cadence and at most 6 model attempts/day still apply, unless a stricter existing baseline cap takes precedence; ≤60 baseline cap remains an absolute safety ceiling.
- The 30 forecasts are time-distinct but 24h outcome windows may overlap, so **do not call 30 forecasts statistically independent observations**. Evaluate autocorrelation and overlapping returns or report uncertainty explicitly.
- New eligibility requires truly new qualifying RSS news since prior collection. Preserve first-run warm-up, source allowlist, model JSON validation, fixed prompt input bounds, durable attempt journal, and post-inference entry quote. Never synthesize news or backfill forecasts.
- Future outcomes at +1, +4, +24 h recorded only after their horizons and an explicit time safety guard; retry missing quote/labels without losing the attempt state. Stop label-only timers only after all expected outcome rows are durably saved.
- Avoid concurrent runs across duplicate plugin instances: SQLite transaction + slot claims + file locking across workers. Restart persistence; no reset on app reload. Hard stop on 30 predictions **or** 36 model attempts, or any critical anomaly. Distinguish saturation of model cap from outcomes still awaiting labels.
- No copied raw user messages, credentials, complete prompts or unredacted news content in Git or diagnostic reports; Git stores code, tests, synthetic examples and aggregate counts only.

## Required analytics

For each issued forecast: exact UTC timestamp, direction, confidence, model identity, entry quote and quote timestamp, news batch hash, result horizon, and actual price observed at/after due time. Keep immutable prediction records and no look-ahead.

Report:
1. Counts: eligible headlines, batches prepared, model attempts, valid predictions, rejected responses, pending/unknown model attempts, completed/expected labels, schedule/clock/Binance errors, duplicates prevented.
2. Directional accuracy for non-FLAT predictions and FLAT as its own category; confidence calibration only if interpretation is supported.
3. Hypothetical **gross** and **net** returns for 1/4/24h separately; baseline long HODL over matching windows, flat cash baseline (0), and a reproducible naive directional strategy. Costs as explicit scenarios (e.g. **0.10%, 0.20%, 0.30% round-trip**) with documented scope (fees/slippage). Funding, borrow fees, market impact and execution gaps are excluded unless simulated with verifiable assumptions.
4. Wins/losses, median/mean, drawdown/volatility sensitivity, confidence intervals cautiously stated with overlapping windows; explain why favorable samples do not prove economic profitability.
5. Immutable export and SQLite read-only SQL/status command; track pilot versus later Phase 3 observations separately.

## Delivery sequence

1. **Preflight on Kali (read-only):** inventory exact deployed NEWS v1.3.1 code/blob hashes, baseline dependency/DB schema, plugin configuration and gateway model identity; identify safe DB isolation and restart/install conditions. Send findings in Phase 3 Git dialogue. No live model call or mutation during preflight.
2. **Implement branch + offline tests:** ChatGPT owns source edits and Git commits. MT runs isolated verification/probes against ChatGPT-published pinned candidate with fake RSS, mock MT SDK, public quote fixtures, temp DB and simulated restarts/errors. Test at least stop 30/36, unknown SDK fail-closed, claim isolation, schedule after restart, 1/4/24 timing, no double scoring, fee formulas, no touch of Phase 2 DB.
3. **Controlled trial:** after passing tests and validated source/config, install as a new separate Phase 3 component; first conduct one synthetic validation with no Phase 3 prod DB effects, then ONE manual real forward cycle if eligible; compare SQLite and MT call counters before enabling bounded automatic sampling. The owner is responsible for a desktop app restart if a new plugin module requires it.
4. **Bounded automatic paper sampling:** enable scheduling with explicit caps and on-disk stop state, collect new forecasts when eligible until cap; finish 24h labels before reporting final evaluation.
5. **Evidence / rollback:** log sanitized checks, preserve backups, report exact installed source SHA, no trades, rollback procedure and any blockers. Never silently restart a live MT session.

Initial Phase 3 deliverable is a verified implementation candidate and safe startup procedure; **not a claim of revenue or validated trading edge**.

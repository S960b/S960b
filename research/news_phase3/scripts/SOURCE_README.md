# NEWS Intelligence Phase 3 — isolated, paper-only candidate

**Release state:** SOURCE CANDIDATE ONLY. Not installed, not scheduled, not tested on Kali yet, no predictions collected. Must pass offline verification, source provenance review and a separate owner-approved controlled startup. No trading endpoints or private exchange APIs.

Owner-approved plan: [NEWS_PHASE3_PLAN.md](../../docs/NEWS_PHASE3_PLAN.md). Task dialogue: `task_e6f8b71ffee2f3a332c2a4adc6f5be57`.

## Provenance and separation

- `news_p3_base.py`: security-reduced source derived from the *entire*, MT-verified `/home/kali/research/news-phase2/news_phase2.py` baseline; source SHA256 `5f11cb76549e0259d93db62c8e3f3d3016732ab145eb3dce3befe1f29c1cec1b` (16,929 bytes), handed off in Phase3 MT dialogue seq6. It retains **public** RSS, Binance server-time/quote/klines, input validation, SQLite table schema, scoring/labels and budget methods; standalone direct DeepSeek API/key/forecast/CLI paths removed.
- `news_p3_cycle.py`: separate adapted MultiTool session workflow using that isolated baseline module. Hard safety caps: **30 valid forecasts** or **36 cumulative model_attempt events** (whichever first). It enforces a 4-hour gap between accepted forecasts, at most six attempts per rolling 24 h and the original 60-forecast absolute baseline cap.
- `news-p3-helpers.mjs`: SDK wrapper; requires an actually observed `{providerID,modelID}` pair from the **assistant** message metadata. It never substitutes a hard-coded model label. If model metadata is absent or SDK result ambiguous, the reserved batch stays pending for manual reconciliation; the prompt is not repeated.
- `news-p3-plugin.mjs`: exported module names remain **only** `default` and `server` for the MultiTool legacy plugin loader. Both automatic predictions and automatic labels require **distinct opt-in** config keys (`auto_phase3_enabled`, `label_phase3_enabled`); `dry_run` is true unless explicitly set false. Unlike Phase2, the schedule does **not** stop after the first valid prediction.
- `news_p3_eval.py`: read-only analysis of gross returns and 0.10%, 0.20%, 0.30% assumed round-trip cost scenarios. Comparisons: model, long BTC matched-window HODL, always-short naive direction and cash. Overlap count and lag-1 descriptive correlation included, without independence claims.
- `test_news_p3_cycle.py`, `test_news_p3_plugin.mjs`: isolated regression tests. **Test counts remain unverified until MT executes them.**

## Isolation contract

The new on-device files belong under **`/home/kali/research/news-phase3/`**. The SQLite path must be a private **`~/.local/share/news-phase3/news.sqlite`**, mode 0600 with private directory 0700. The scripts never import from the live Phase2 install or reuse Phase2's DB. The Phase2 pilot and its backups are untouched.

Do not copy the old v1.2/v1.3 plugin entry or change existing plugin registrations. The Phase3 plugin uses distinct triggers `.mt-news-p3-run-now` and `.mt-news-p3-probe-now` and logs `news-p3-plugin.log`. Do not use old `news-phase2.timer` or `news-phase2.service`. There is no direct DeepSeek API key; MT uses the already-configured gateway. Only public Binance data endpoints and four preapproved RSS sources are used.

## Offline verification (before any deployment)

From a disposable directory containing **only** this package:

```sh
python3 -m py_compile news_p3_base.py news_p3_cycle.py news_p3_eval.py test_news_p3_cycle.py
python3 -m unittest -v test_news_p3_cycle.py
node --check news-p3-helpers.mjs
node --check news-p3-plugin.mjs
node --test test_news_p3_plugin.mjs
```

No real RSS, Binance, private DB or SDK sessions during offline tests; inspect test fixtures before running. Check source SHA/tree exactly and compare the live Phase2 file checksums pre/post. Do not install just because syntax checks pass.

## Read-only production reporting after authorized deployment

```sh
python3 news_p3_cycle.py status_ro --db ~/.local/share/news-phase3/news.sqlite
python3 news_p3_cycle.py export_ro --db ~/.local/share/news-phase3/news.sqlite
```

These CLI modes use SQLite URI `mode=ro`, do **not** initialize tables or claim model budgets, and return aggregate metrics / a sanitized JSON observation export. The export deliberately excludes news titles, summaries, rationales, raw prompts, API credentials and session IDs. The legacy `base.report` fixed-0.30% summary is labeled as a legacy component; official comparisons use the scenario-based `evaluation`.

## Startup gate

1. **MT offline tests** on an exact candidate Git commit in a disposable worktree, plus review the actual SDK per-session model metadata shape. If unsupported, mark `MODEL_IDENTITY_UNVERIFIED`; do not guess a model and do not run a real prediction.
2. Owner approves *separate* Phase3 plugin install, configuration and controlled MultiTool desktop restart; backup and rollback paths must be documented. Initially `dry_run:true`, `auto_phase3_enabled:false`, `label_phase3_enabled:false`.
3. Optional synthetic test only with explicit permission; it does not write the Phase3 SQLite DB. Then one manual genuinely eligible live-forward cycle (still paper-only) after owner approval, with time/quote checks and durable counters.
4. Only after confirmed one-cycle validation enable bounded automatic sampling and label schedules, maintaining the 30/36 stops and pending-state fail-closed semantics. Never fabricate news or retroactively assign an entry price.
5. At any anomaly: disable Phase3 schedules, preserve DB/logs, do not retry uncertain model calls. The original Phase2 plugin/config/service must remain unchanged.

**Accounting note:** The old Phase2 0.003 (0.30%) charge is a chosen hypothetical entry/exit fee+slippage scenario, not an observed broker cost. The previous mention of 0.19% was inconsistent. Phase3 recomputes every scenario from the raw observed BTC return; this experiment is not evidence of profitability.

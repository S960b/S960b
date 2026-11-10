# TP/SL Lab v0.12: TRAIN-only entry information diagnostic (pre-registered design)

## Status of older hypotheses

v0.10 continuous EMA50/200 and Donchian20 minute entries: failed the predefined profitability criterion for both BTC and ETH. v0.11 event-only EMA crossover / first Donchian breakout: also failed the predefined criterion, even though trading frequency dropped. None were selected for 2025 validation. Do not rehabilitate or reselect either experiment based on v0.12 outputs.

## Scope and scientific question

Before attempting new stop/target rules, ask whether previously defined entry **events** contain any *forward price information* beyond random times on the same trading day. This is exploratory work on the reused TRAIN sample (2024-04, 2024-05, 2024-06); it is NOT an independent finding, a finished strategy, a TP/SL backtest, a trade-frequency comparison, or evidence of a live trading edge. No trading logic or parameters from v0.11 are tuned.

## Frozen analysis (before seeing this new output)

- Data: 6 prior checksum-validated Binance Spot monthly BTCUSDT and ETHUSDT 1m archives, April–June 2024 (131,040 candles per symbol). Loading uses the existing `load_split(root, 'train')` function; no other split is permitted by this diagnostic CLI.
- Exactly four event streams per symbol: `ema_cross` and `donchian_first`, each on 1-minute or UTC-completed 15-minute bars. Identical, unchanged event implementations from v0.11.
- Exactly three independent fixed holding horizons: 60, 240, 1,440 minutes after the entry OPEN. Signal from a completed bar permits hypothetical entry at the NEXT one-minute OPEN. Hypothetical exit uses the OPEN exactly H bars later. No stops, no targets, no liquidation and no actual orders.
- Random benchmark: for each event, 32 seeded uniform random *eligible* entry OPENs on the **same UTC day** and at the same timeframe completion phase (for 15m, entry minute 00/15/30/45); actual event entry timestamps are excluded from the controls. For an event with no complete H-minute future, omit it from that horizon. Report number omitted.
- Compute simple forward return and average paired event minus random-control forward return, in basis points. Also show fixed baseline and stress two-sided cost-adjusted event and control forward returns. Costs do **not** model future exchange execution or isolated order-book slippage. Fee assumptions: 0.05% baseline or 0.10% stress per side; slippage assumptions: 0.01% baseline or 0.03% stress per side, applied adversely.
- Report approximate percentile 95% intervals from 500 deterministic bootstrap resamples of 7-day UTC blocks of *paired differences*. The bootstrap is a sensitivity diagnostic only (13 calendar weeks, serial dependence and a reused training set). For fewer than 5 event-bearing weeks, report no interval.
- Fixed 24 result rows = 2 symbols × 2 rules × 2 timeframes × 3 horizons. Never select a strategy or trigger validation automatically, even if the descriptive intervals exclude zero.

## Interpretation guardrails

Positive raw returns can simply reflect market drift during bullish days. Compare event returns against same-day controls, not against zero only. Cost-adjusted mean forward returns do not represent actual compounded PnL or net profits from tradable positions: events can overlap, trades are hypothetical, there is no capital constraint, and prices are 1m OPENs. Near-market-close/day boundaries and intraday regime differences can still confound random controls. The 7-day block confidence interval is approximate and does not correct for all dependence/multiple comparisons, particularly at the 24h horizon.

Do not make an out-of-sample or investment claim from these data. If exploratory tests are negative/inconclusive, the next step is to reconsider the source of alpha, not to maximize the same training results. The 2025 validation and 2026 holdout remain sealed and require a new frozen, independently motivated hypothesis and separate approval.

## Acceptance

A new isolated venv, exact source tar SHA, all pytest tests and independently constructed unit tests; source archival hashes, 24 rows, no `validation` or `holdout` stage, unchanged historical files, no order/API connections, version provenance, a new non-overwriting report and SHA. Do not run any new TP/SL grid or optimization.
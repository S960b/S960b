# TP/SL Lab v0.13.0: Frozen aggressor trade-flow information diagnostic

**Status:** exploratory TRAIN-only **research hypothesis**, authored after v0.10/v0.11 strategies and v0.12 price-only signals were negative. This is NOT a trading strategy, profit forecast, independent confirmation, or selection exercise. No exchange/network/order code.

## Data provenance and partitions

Use **only** previously audited Binance public **Spot** monthly **BTCUSDT, ETHUSDT 1-minute** archives for **2024-04, 2024-05, 2024-06** (91 days per symbol). Each input ZIP must match its official `.zip.CHECKSUM`. Full OHLC and timestamp integrity are reverified by the immutable v0.10 loader, then the exact same ZIP/CSV SHA is rechecked when loading raw quote-flow columns. No replacement, repaired or synthetic market data. **2025 validation and 2026 holdout may not be opened.** CLI exposes only `--stage train`.

Binance Spot 12-column kline fields (zero-based): 0 `open_time`, 5 base volume, 7 quote-asset volume, 8 trades, 9 taker-buy base volume, 10 taker-buy quote volume. **All trade-flow fields are volumes of executed trades, not order-book imbalance**. Verify Decimal values, finite and nonnegative, buy volume not exceeding total, and strictly positive quote/base volumes. The ratio `q = taker_buy_quote_volume / quote_asset_volume` uses Decimal; for 15m it is the **ratio of sums**, never the average of 1m ratios.

## Frozen design, fixed before calculating results

- Symbols: BTCUSDT and ETHUSDT Spot. Intrabar source bars: 1m; **complete UTC-aligned signal bars**: 1m and 15m.
- **High buy pressure**: `q >= 0.80` for 1m; `q >= 0.60` for 15m.
- **High sell pressure**: `q <= 0.20` for 1m; `q <= 0.40` for 15m. The sell-side is a **descriptive directional check**, **not** an executable Spot short.
- Neutral comparator for both timeframes: `0.40 <= q <= 0.60`, excluding observations already classified extreme. Controls are drawn from the **same UTC entry day**, same signal timeframe, with deterministic seed `20261110`, **16 draws with replacement per event**. If no same-day neutral comparator exists, report skipped event; do not fabricate.
- Signal information becomes available only after the signal bar closes. Hypothetical entry price is **OPEN of immediately following 1m bar**. Future prices are **OPEN** after exactly 60 or 240 additional 1m bars. Entries lacking the complete future horizon are excluded with counts.
- For each event group, compare arithmetic mean event forward return with arithmetic mean same-day neutral control return. For high-buy event use `signed_lift = event_return - neutral_return`; for high-sell event use `signed_lift = neutral_return - event_return`. Report bps. No trades are aggregated across overlapping forward windows; these are NOT independent executable trades.
- Baseline fee 0.05% and slippage 0.01% per side; stress fee 0.10% and slippage 0.03% per side. Only high-buy rows include fixed-horizon **Long-only cost proxy**; no short-selling PnL calculated. Costs are not market/account fee quotes.
- 2 symbols × 2 timeframes × 2 sides × 2 horizons = **16 fixed results**; no optimizer, parameter sweeps, forecasts, trade execution, or TP/SL. Minimum 30 eligible paired events to mark a cell as a sufficiently populated *diagnostic*.
- Approximate 95% interval: 500 deterministic resamples of UTC-aligned 7-day blocks, >=5 active weeks. **13 weeks is a small number; the intervals are approximate, and the 16 comparisons are not multiplicity-adjusted.** No p-values or strong statistical guarantees.
- **Pre-declared main question:** do **15-minute high-buy** extremes predict more favourable 60-minute *next-open* returns than neutral flow on **both BTC and ETH**? All other 14 rows are secondary descriptions, not candidate strategies. Even a positive answer on this reused 2024 training window cannot establish out-of-sample alpha.
- Fixed `no_orders=true`, `validation_unseen=true`, `holdout_unseen=true`. Any final operational strategy needs a separately designed and frozen protocol, more independent data and execution tests.

**Potential confounders:** trade-flow imbalance can correlate with same-bar price moves, market regime, time-of-day, volatility, clustered events, drift, and liquidity. Matching same UTC day does not establish causality or eliminate lookahead from other market microstructure features. Bootstrap and signal eligibility do not imply profit.

## Acceptance vs interpretation

The *software acceptance* criterion is 265 local tests plus independent tests by MT of field integrity, complete 1m/15m aggregation, timing, controls, no-lookahead, deterministic output, sealed periods, and no exchange IO. The *market hypothesis* receives **no automatic pass or validation permission**: ChatGPT will interpret exploratory results and decide whether an entirely new frozen test is justified. Negative/null results are recorded without modifying thresholds.
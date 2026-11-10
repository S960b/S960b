# TP/SL Lab v0.10.0: Preregistered offline BTC/ETH Spot study

## Scope

Research only. The code contains **no network client, order API, exchange keys, leveraged trades, futures funding or liquidation mechanics**. The `tpsl_lab.spot_study` module always operates long-only on Binance **Spot** BTCUSDT and ETHUSDT monthly 1-minute public archives, with one independent fixed 100 USDT nominal position at a time, 1000 USDT hypothetical starting equity per symbol. These are *model assumptions*, not a live execution guarantee. Price precision, latency, changing fees and liquidity remain unmodelled.

## Frozen preregistration (before any TRAIN/VALIDATION strategy evaluation)

- TRAIN: UTC 2024-04, 2024-05, 2024-06.
- VALIDATION: UTC 2025-04, 2025-05, 2025-06; one subsequent use only for the selected TRAIN configurations.
- FINAL HOLDOUT: UTC 2026-07, 2026-08, 2026-09. **No code path to read or evaluate this split** in the current release. The holdout must remain unopened until a separately frozen final protocol.
- Assets: BTCUSDT + ETHUSDT. Signal rules: EMA50/200 and previous-20-bar Donchian breakout. Short signals ignored. Signals generated on completed 1m candle; entry at next open; stop-first on ambiguous same-minute OHLC.
- TP grid = `{0.5%, 1%, 2%}`; SL grid = `{0.5%, 1%, 2%}`. Total 18 candidate pairs (2 rules x 9 TP/SL). No parameter sweeps outside this grid in this phase.
- Baseline cost scenario: fee 0.05% per side; adverse slippage 0.01% per side. Stress scenario: fee 0.1% per side; adverse slippage 0.03% per side. These are explicit hypothetical costs, not account quotations.
- Controls: no-trade and random entries with fixed seeds 7,17,29, at TP=SL=1%, baseline costs. A control is **not** a candidate for live trading.
- TRAIN eligibility: at least 30 closed trades **per asset**; **positive marked-to-market net PnL after costs on both assets**. Rank eligible candidates by their *worse* asset net marked PnL, tie-break by max drawdown, then fixed string ID. Select up to **three** configurations. If none qualifies, **do not use VALIDATION**; declare no confirmed candidate and revisit design using TRAIN only.
- VALIDATION: only previously selected 1–3 configurations, on both assets, under baseline and stress costs. Do not rerank by validation performance. No holdout data or backtest results may be consulted in development.
- Numeric performance estimates are model results, not exchange fills or expected profits. Include `open_position_at_end` and marked equity in all comparisons. No multiple-testing claims from the top training candidate. No futures leverage or short positions.

## Data integrity

The user-side executor MT already reported a separate verification of all 18 official ZIP and `.CHECKSUM` files. In this release the study **independently rechecks each consumed file** against the accompanying CHECKSUM and validates one safe ZIP CSV member, exactly 12 Binance columns, millisecond dates in 2024, microsecond dates in 2025, minute-aligned timestamps, the exact expected number of rows, OHLC and volume bounds, and cross-month continuity. The module only loads TRAIN or VALIDATION months; 2026 holdout is never read. There is no gap filling or date conversion using floating-point arithmetic.

Use the original source archives under `/home/kali/research/tpsl-spot-history-v10` (which contains `zip/` and `checksum/`). The module searches for one exact expected filename and rejects duplicates. It writes only a **new** JSON report path, refusing overwrite. It does not modify raw ZIP or CHECKSUM.

## Execution: separate authorization of stages

```bash
# In NEW tpsl-lab-v10 workspace, after checking archive checksum:
env -u APPDIR -u APPIMAGE /usr/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q

# MT: TRAIN ONLY on the first handoff:
.venv/bin/python -m tpsl_lab.spot_study \
  --stage train \
  --data-root /home/kali/research/tpsl-spot-history-v10 \
  --out /home/kali/research/tpsl-results-v10/train_v10.json

# VALIDATION is a distinct later step, after ChatGPT inspects/fixes top candidates:
.venv/bin/python -m tpsl_lab.spot_study \
  --stage validation \
  --data-root /home/kali/research/tpsl-spot-history-v10 \
  --train-report /home/kali/research/tpsl-results-v10/train_v10.json \
  --out /home/kali/research/tpsl-results-v10/validation_v10.json
```

Outputs include complete scenario/parameter definitions, raw SHA256 provenance for every consumed monthly file, all train candidates and controls, top selections, risk/trade statistics, and an explicit holdout-unseen statement. MT must not run VALIDATION until the following instruction; no test should ever run HOLDOUT at this stage.

## Interpretation

Even a positive training result may be pure chance or a product of optimizing across 18 candidates. The selected candidates must survive a *single* later validation period on two assets and realistic cost sensitivity; otherwise report failure. Final holdout is reserved for one genuinely predeclared final candidate only. Trading or recommending real capital requires further analysis of execution, regime shifts, robustness and risk limits. No instruction to automate trading is implied.
# TP/SL Lab v0.11 — NEW post-v0.10 exploratory hypothesis

## Why this is a separate experiment

v0.10 was fully rejected on the April–June 2024 BTC and ETH Spot training data: all 18 tested configurations were negative after fees and adverse slippage. The latest read-only audit showed frequent repeated signals and high turnover. Those findings **inspired** this new hypothesis. Reusing April–June 2024 as TRAIN therefore provides only exploratory evidence; it is not an independent proof of a newly discovered trading edge. Independent validation remains April–June 2025. July–September 2026 remains sealed final holdout, not accessible through this module.

## Frozen choices BEFORE the v0.11 train run

1. Market: public Binance BTCUSDT and ETHUSDT Spot 1m OHLCV, long-only, no leverage or shorting, complete daily/monthly UTC checks and official ZIP checksums. Entry and exit execution always on 1m reference candles.
2. Signal timeframe: 1m or 15m. The 15m bar is only built from **fifteen completed** 1m bars with UTC aligned boundaries. Signals act at next 1m open, never within the signal timeframe bar.
3. Entry rule A: upward **crossing event** of EMA50 over EMA200, only when close > fast EMA at the cross and at least 200 signal bars accumulated. Only one trigger per cross; persistent trend bars do not re-trigger.
4. Entry rule B: close above previous 20 highs of complete signal-timeframe candles **only when previous signal bar did not meet its own breakout condition**; at most one trigger per 60 minutes, no repeated consecutive breakouts.
5. Stop / target: either TP=2%, SL=1% or TP=3%, SL=1%. Thus exactly 2 rules x 2 signal timeframes x 2 pairs = **8 configurations**, all preregistered.
6. Capital: start $1,000, position notional $100, one open position per symbol. Baseline fees 0.05% each side, adverse slippage 0.01% each side. Stress fee 0.10% each side, adverse slippage 0.03% each side. Stop-first on ambiguous 1m OHLC, gap-aware entry/exit. Market-order proxy, not a historical order-book fill model.
7. Selection after TRAIN: at most two frozen candidate IDs, require >=30 closed baseline trades per symbol and positive marked net PnL in **both BTC and ETH in both baseline and stress cost scenarios**. Rank by the worst of the two stress returns, then lower worst drawdown, then config ID. If none meet conditions, do not run validation. No thresholds weakened retrospectively.
8. After frozen qualifying TRAIN, authorize 2025 validation only in a separate instruction. To validate, pass the exact independently recorded TRAIN JSON SHA256 as `--train-sha256`, alongside `--train-report`. Any mismatch fails before the 2025 dataset is loaded. At most one evaluation of frozen candidates, with no re-selection. 2026 holdout must remain sealed until separately approved. No trading recommendation until later independent evidence.
9. Explicit restrictions: all source code is offline, no exchange/account API, no live/testnet/paper orders, no NEWS modifications and no hidden tuning/optimizers. Results of training cannot be described as forward profitability.

## Runner

Install in a fresh environment: `python -m pip install -e '.[test,parity]'`. First run `python -m pytest -q`. Then in an **already existing new output directory**:

```bash
python -m tpsl_lab.event_study --stage train \
  --data-root /home/kali/research/tpsl-spot-history-v10 \
  --out /home/kali/research/tpsl-results-v011/train_v011.json
```

Do NOT run validation without a separate instruction. `--stage holdout` is forbidden and unsupported.
# Trading Research Archive

[**English**](README.en.md) | [Русский](README.md)

## Read the audit before reviewing individual experiments

- [Consolidated trading research audit (10 November 2026, Russian)](../TRADING_RESEARCH_AUDIT_2026-11-10.md): documented implementation and methodology errors, limits of evidence, and corrections to earlier independent reviews.
- [Research validation protocol (Russian)](../TRADING_RESEARCH_PROTOCOL.md): data integrity, accounting, execution assumptions, holdout, risk and GO/STOP gates.

**Main conclusion:** as of the recorded evaluations, no examined strategy has independently established stable, reproducible net profitability suitable for live trading. This does not prove that every strategy family is unprofitable. Some specific implementations tested negatively, some data was incomplete, and others remained unfinished.

This is an archive of research rather than a collection of ready-to-run trading bots. Source material is kept to make negative and inconclusive results inspectable.

**Catalog snapshot:** 10 November 2026, with ETH Grid updated through 12 November. Historical entries are not live process-status reports. No investment advice or real-order execution.

| Project | Investigated mechanism | Recorded result / evidence limit |
|---|---|---|
| [NEWS Phase 2](news_phase2/) | News → BTC forecasts over 1/4/24 hours | No confirmed net signal; incomplete observations at the recorded snapshot |
| [NEWS Phase 3](news_phase3/) | 30 sequential BTC paper forecasts | Incomplete at the recorded snapshot (7/30); profitability not established |
| [NEWS Phase 4 v1](news_phase4_v1/) | News, market and social context | 10/10 neutral decisions; no demonstrated trading signal |
| [NEWS Phase 4 v2](news_phase4_v2/) | NEWS versus NEWS+MARKET | Too few assessed pairs to establish improvement |
| [TP/SL Lab](tpsl_lab/) | BTC/ETH TP/SL, EMA/Donchian and aggressive trade flow | Archived / no robust net edge in tested configurations |
| [ETH Grid](eth_grid/) | ETH grid proxy versus ETH HODL and a static 50/50 ETH/USDT portfolio | **Closed / NO-GO, 12 November:** all six settings underperformed 50/50 on the 30-day period and all six on the separate frozen 7-day forward; not independent across settings |
| [Binance Copy](binance_copy/) | Follower execution and leader-performance replication | Leader returns do not establish follower returns |
| [Hyperliquid Wallets](hyperliquid_wallets/) | Public wallet fills, realized PnL, fees and funding | **Archived / insufficient data:** both 2,000-fill snapshots are partial; no complete wallet-return estimate |

Other archived investigations are [Pionex](pionex/), [SafeTrade](safetrade/), [SafeTrade exit premium](safetrade_exit_premium/) and [Paper Seven-Day](paper_seven_day/). Their original reports and limitations remain available.

## Archive standards

1. Distinguish a **failed specific hypothesis**, **insufficient data**, and an **unfinished evaluation**.
2. State test windows, benchmarks, costs and assumptions; separate backtests, holdout, paper and live results.
3. Preserve original scripts and provenance when available. New reconstructions must not be presented as the original executed code.
4. Do not publish API secrets, private credentials or unnecessary raw account information.
5. A passing code test is not proof of financial performance. Report mark-to-market portfolio value, open positions, costs and funding where material.
6. No live trading is authorized by these research artifacts. Hyperliquid analysis requires complete and auditable data before any new economic conclusion.

**Purpose:** make previous attempts easy to inspect, identify what was actually tested and avoid repeating known methodological mistakes.

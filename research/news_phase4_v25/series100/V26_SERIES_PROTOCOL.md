# NEWS Intelligence v2.6 — preregistration for the 14-RSS / 3-symbol paper series

**Status: DRAFT / NOT ACTIVATED.** This is a distinct prospective cohort from
v2.5's BTC-only, four-source protocol. Do not mix results across protocols.
This file does not grant permission to make a model call or a real trade.

## Scope

- Exactly 14 explicitly allowlisted RSS/Atom XML sources as defined by
  `rss14_adapter.RSS14`, using public metadata (headline, summary, URL, date).
  No JSON status API, websocket, article crawling, paywall bypass or backfill.
- One qualifying occurrence routes to **exactly one** market:
  `BTCUSDT`, `ETHUSDT`, or `SOLUSDT`. Macro/global primary-source events
  use BTCUSDT as the prespecified benchmark only when the deterministic
  classification permits. Multiple-asset/ambiguous items are excluded.
- Scheduled maintenance, testnets, peripheral status pages, stale and editorial
  items are rejected by the versioned `rss_noise_filter`.
- The RSS original bytes, first observed timestamp, URL and item's published
  time are verified against the original immutable feed archive and the
  separate metadata sidecar. Never rewrite publication/observation times.
- Preserve v2.5 Tier-2 **per-cohort freshness** budgets and separately enforce
  the filter's proposed `MAX_AGE_MS=600000`. Neither threshold is relaxed.

## Prediction contract

- DeepSeek `deepseek-v4-flash`; exactly **three sequential arms**, N (news
  only), NM (news + frozen market), M (frozen market only).
- Each arm receives the **same ticker** in its prompt, system header and JSON.
  M must have no news, title, summary, event ID or source.
- All three arms share one frozen snapshot of exactly 26 fully closed hourly
  Binance spot candles strictly before the pre-decision hour cutoff.
- Entry quote for that ticker comes from the public Binance endpoint **after**
  all model responses. The outcome uses the first properly closed 1-minute
  candle covering `entry_observed_ms + 4h`, with drift in [0, 60000 ms).
- Market requests record the selected ticker and full public Binance request
  URL, response byte SHA-256, raw bytes, timestamps and immutable evidence
  filename. A Binance `klines` body does **not** independently identify its
  symbol: URL-binding is local operational evidence, not an exchange signature.
- Evidence tier stays `observational_tier2`. A tool-free prompt or a gateway
  response with no tool calls is not proof of upstream isolation.

## Immutable limits and process

- Up to **100 sealed events / 300 model-call reservations** globally, including
  failures and ambiguous calls. The budget is reserved transactionally
  **before** each model request. An ambiguous call is never retried.
- One semantic news cluster can be claimed once; a claim binds one symbol,
  RSS original, prompts, market snapshot and protocol digest before a call.
- Preserve immutable frozen inputs and exact model request/response bytes,
  provider call metadata, output, attestation chain and source witnesses.
- At most one process may execute a series event at a time.
- Pending labels may settle after the series is disarmed. Missing market data
  stays pending/censored and must never be manufactured.
- Primary evaluation is directional accuracy among non-FLAT actions, paper
  return with **6 bps round-trip cost** compared with FLAT and M; report
  FLAT frequency, censored rows and counterfactuals separately.
  Secondary metrics: Brier, MAE. Do not treat censored outcomes as zero PnL.
- LONG iff `p_up >= 0.55` and expected bps `> +35`; SHORT iff
  `p_up <= 0.45` and expected bps `< -35`; else FLAT.
- No actual orders, balances, leverage or execution APIs.

## Activation gate

A separate, owner-approved and independently audited **v2.6** activation
must declare the exact SHA-256 of this protocol, model ID, prompt version,
`max_events=100`, `max_model_invocations=300`, paper-only,
`no_calendar_expiry=true` and `expires_ms=null`. The gate file
`V26_TRIGGER_ENABLED` is absent by default. A failed gate check means
**zero model calls**. A distinct RSS collector may operate in paper capture
mode without enabling the model gate.

Stop on owner revocation, safety failure, 100 seals, 300 calls or exhausted
operational integrity. Existing v2.5 release, databases, GUI, timers and
experimental observations remain untouched.

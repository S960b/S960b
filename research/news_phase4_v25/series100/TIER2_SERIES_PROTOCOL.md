# NEWS Intelligence v2.5 — Tier-2 series 100/300 preregistration (proposed)

Status: DRAFT / NOT ACTIVATED. Separate from the immutable one-shot trial.
This protocol does NOT assert known_zero. Upstream tool isolation remains
unverified; evidence tier observational_tier2.

- One news event is frozen before all model calls.
- Exactly three arms, in order: news (N), news_market (NM), market (M).
- Model: deepseek-v4-flash. No browsing, no tools, no external content beyond frozen payload.
- Existing v2.5 prompt texts and action thresholds must remain unchanged.
- BTCUSDT, 4-hour horizon, closed 1-minute Binance candle, drift in [0,60s).
- 6 bps round trip. LONG iff p_up >= 0.55 and expected bps > +35.
  SHORT iff p_up <= 0.45 and expected bps < -35. Else FLAT.
- News only from existing v24 feed adapters with validated byte-for-byte archived
  original RSS, matched item and existing strict freshness windows.
- Semantic deduplication by sha256(title|published_ms), also unique event_id.
- Cap: at most 100 SEALED events, and at most 300 durable model invocation
  reservations across the entire series, including failures and unknowns.
  Budget is counted BEFORE sending each network request; no retry after
  ambiguous outcomes or crashes. A partial event remains excluded from the
  model accuracy denominator but its cost and reason remain in the ledger.
- No fresh eligible news means no invocation. No retroactive backfill.
- Claim event and cluster atomically before work. Freeze file write-once.
  Sealing binds raw request/response bytes and attestation evidence.
- Labels are independently settled at the due time; missing/invalid labels
  remain censored or pending, never fabricated.
- Activation has explicit start and expiry timestamps chosen before GO. At
  expiry stop new inferences and report attained n, even if below 100. Due
  +4-hour labels already sealed remain eligible to settle after expiry.
- Stop on 100 sealed, 300 calls, expired activation, explicit revocation,
  unexpected integrity failure, or unrecoverable operational condition.
  No real orders, positions, leverage, trades or wallets.
- First-trial activation, DB, frozen files and evidence remain untouched.
- Primary evaluations: direction accuracy among actual LONG/SHORT decisions,
  rate of FLAT, paper PnL after costs versus FLAT baseline and market-only arm.
  Brier/MAE are secondary diagnostics. No cherry-picked exclusions.

Activation requires a separate independently audited release/commit, runtime
preflight, and explicit GO. This file alone never enables a model call.

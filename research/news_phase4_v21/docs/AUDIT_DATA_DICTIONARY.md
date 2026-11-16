# NEWS v2.1 — Data dictionary

All timestamps are **integer milliseconds since the Unix epoch, UTC**.
Every file in this bundle is derived from the production SQLite databases by
`scripts/v21_export.py` / `scripts/v21_snapshot.py`, both read-only.

## `data/export_YYYYMMDD.json`

| field | type | meaning |
|---|---|---|
| `kind`, `version`, `taken_ms` | str/int | export identity; `taken_ms` is the as-of instant |
| `events.quick_check` | str | SQLite `PRAGMA quick_check` result of the events DB |
| `events.rows[]` | obj | one row per collected news item |
| `.event_id` | str(64) | sha256 of the normalised item; stable identity |
| `.source` | str | publisher slug (`coindesk`, `bls`, `sec`, …) |
| `.published_ms` | int | publisher-stated publication time |
| `.observed_ms` | int | when our collector first saw the item |
| `.source_kind` | str | `primary` / `secondary` |
| `.cohort` | str | `fast_verified` (≤5 min), `context_news` (≤2 h), `history_only` |
| `.title` | str≤180 | truncated headline, for verification only |
| `.url` | str/null | canonical link if the feed supplied one |
| `paper.records[]` | obj | one sealed decision per event, insert-only |
| `.event_id` | str(64) | → `events.rows[].event_id` |
| `.cutoff_ms` | int | last fully closed 1h candle boundary (market features) |
| `.reserved_ms` | int | real instant the quota was reserved (decision time) |
| `.completed_ms` | int | when the pair was sealed |
| `.provider_id`, `.model_id` | str | observed model identity from the response |
| `.seal_sha256` | str(64) | digest over prompts, both response hashes, quote, cutoff |
| `.synthetic` | 0/1 | 1 = offline fixture, excluded from production metrics |
| `.state` | str | `complete` |
| `paper.arms[]` | obj | one row per arm: `news` (N) and `news_market` (NM) |
| `.prompt_sha256` | str(64) | digest of the sealed prompt that was sent |
| `.p_up` | float | predicted P(BTC higher after 4 h) |
| `.expected_return_bps` | float | signed expected move, basis points |
| `.uncertainty` | float 0..1 | model's own uncertainty |
| `.action` | str | `LONG_PAPER` / `SHORT_PAPER` / `FLAT` (deterministic gate) |
| `.response_sha256` | str(64) | digest of the raw model text, for audit |
| `.completed_ms` | int | that arm's own completion time |
| `paper.entries[]` | obj | true entry price: public BTCUSDT ticker **after both arms** |
| `.price`, `.observed_ms`, `.source` | | price, as-of time, endpoint name |
| `paper.outcomes[]` | obj | 4h labels; empty until due |
| `.due_ms` | int | `entry.observed_ms + 4h` |
| `.exit_ms` | int | close of the first public 1m candle with `close >= due_ms` |
| `.drift_ms` | int | `exit_ms - due_ms`; must be ≤ 60 000 |
| `.method` | str | `binance_public_1m_close` |
| `dispatch.reservations[]` | obj | our research ledger: 2 rows per attempted pair |
| `.state` | str | `reserved` / `completed` |
| `gateway_usage.sessions[]` | obj | **per NEWS session only**, from the gateway |
| `.agent` | str/null | `news-restricted` once the tool ban was live, else `null` |
| `.completions` | int | assistant messages billed; **one reservation can be several** |
| `.tokens_in`, `.tokens_out` | int | provider-reported tokens |
| `.cost_usd` | float | provider-reported cost for that session |
| `.tools` | int | tool invocations in that session; **must be 0 for audit-grade** |
| `refusals[]` | obj | pairs refused; model prose deliberately stripped |

## Accounting rule

Three quantities must never be conflated:

1. **reservation** — one row per arm per attempted pair (our ledger, 2 per pair);
2. **completion** — one assistant message the provider billed; a tool-using arm
   produces several inside a single reservation;
3. **tool invocation** — an external call the model made; any value > 0 in a
   prospective session invalidates that pair.

The daily cap (20 calls, 10 pairs) is **our safety throttle**, never a provider
quota. No provider quota is observable on the host.

## `attempt_reconciliation[]` (added 2026-11-16T13:35Z export)

One row per attempted pair, with the denominator made explicit and with the
**evidence level** of each refusal, because not all refusals are equally
documented.

| field | meaning |
|---|---|
| `event_id` | the event that was attempted |
| `attempted_ms` | first reservation for this event |
| `arm_reservations` | 2 for a complete attempt |
| `status` | `recorded` or `refused` |
| `refusal_reason` | from the trace when one exists, else `null` |
| `evidence_level` | **`trace`** = a row in `finish-refusals.jsonl`; **`reconstructed`** = no trace was written (the tracer did not exist yet), inferred from reservation rows + gateway sessions + absence of a record |
| `recorded`, `synthetic`, `cohort` | outcome of the attempt |

`reconstructed` is published as such, never presented as a raw log. The one such
row is `40d21dd674c8`: the pair was refused before refusal tracing existed, so
its reason is corroborated by two gateway sessions, two reservations and no
record — not by a trace line.

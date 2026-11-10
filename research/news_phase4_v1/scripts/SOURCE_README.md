# NEWS Intelligence Phase 4 (v0.1): offline three-arm comparator

STATE: developed and tested offline. No live Phase 4 plugin, model SDK, social scraping, SQLite writes, network calls, private exchange API or trading. No file or timer in Phase 2 or Phase 3 is touched.

Three information variants: news; news_market; news_market_social. Each variation uses the same event and frozen cutoff. Market and social data must genuinely have been available before that cutoff. Social is optional; missing coverage is recorded and excluded from matched pairs. All observed decisions for an event must have the same verified gateway model and finish before a shared paper entry quote.

## Run

From packages/news_phase4:

    python3 -m unittest -v test_p4_compare.py
    python3 p4_compare.py --input /private/phase4/sealed-events.jsonl
    python3 p4_compare.py --input /private/phase4/sealed-events.jsonl --as-of-ms 1794130000000

The program reads a bounded UTF-8 JSONL and emits aggregate JSON on stdout. It does not write output files, connect to SQLite or contact a gateway. Private input must be assembled only by a future forward-time collector; do not use a historical replay as if it were prospective evidence.

## Expected JSONL event

Every line must contain exactly these top-level keys:
- event_id: 16-64 lowercase hex characters, unique per frozen input event
- cutoff_ms: last UTC millisecond at which inputs could be known
- entry_ms and entry_price: ONE common BTC quote sampled AFTER every included model response
- features: object keyed by news, market, social. Each item contains available_ms and a SHA-256 hex digest, no raw text.
- decisions: object keyed by news, news_market, news_market_social. Each item contains direction (UP/DOWN/NEUTRAL), confidence (0..1), observed model (providerID/modelID), completed_ms and input_hash SHA-256.
- outcomes: zero to three objects with horizon_h (1/4/24), exit_ms (at/after due), exit_price (>0), settled_ms (at/after due+120 seconds).

The evaluator rejects timestamps that imply future feature leakage, quotes preceding model completion, duplicate events/outcomes, ambiguous model IDs or differing models between arms, nonfinite prices, arbitrary extra fields, oversized files, malformed JSON and use of a .sqlite input path. The future collector must independently attest timestamp truth and hash provenance; validation cannot establish that an input timestamp is honest.

Fee scenarios: 0.10%, 0.20%, 0.30% hypothetical round-trip costs for UP and DOWN positions; NEUTRAL maps to flat cash with no cost. Long BTC, always-short and cash are matched-window baselines. Means, hit rates, paired return deltas and counts of overlapping adjacent windows are descriptive, not significance proofs.

## Non-goals

No automatic Phase 3 database import: Phase 3 did not prospectively capture all feature snapshots needed for a paired Phase 4 comparison. No performance claims on synthetic test data. No model calls or autonomous execution in this release. Read docs/NEWS_PHASE4_PLAN.md before integrating collectors and a separate opt-in MultiTool adapter.

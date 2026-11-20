# NEWS v2.6 — remediation of four independent-audit findings

This is an isolated, NOT DEPLOYED development branch based on commit 059026bb66c42833cf0e23426ac1a99d976cd9dd. It does not activate the series, touch v2.5, or change the running RSS-only timer.

## 1. Fixed asset identity

A SQLite UPDATE or DELETE of an existing asset binding is rejected. When a Tier-2 triple is inserted, a second sealed-symbol snapshot is created inside the same transaction. Before entry/exit the binding is checked against the sealed snapshot and prompts hash. This protects against accidental mutation and ordinary SQL rewrites, not against a privileged user dropping all triggers and rewriting all evidence.

## 2. Recheck historical market evidence

Before requesting an exit price, the v2.6 label path verifies persisted hourly/entry witness records, their immutable content-addressed JSON manifests, source URL and symbol, SHA-256 and raw response files. An existing exit witness is also verified. Missing/tampered evidence must never result in a priced label. A local hash does NOT establish exchange-signed provenance.

## 3. Terminal labels

The v2.6-specific label path uses six hours after due time as a hard deadline. Later price successes do not resurrect overdue outcomes. Three genuine transient errors remain bounded. Unexpected validation/program errors are recorded as a separate terminal failed outcome. Market witness corruption produces terminal censored with a diagnostic. Idempotence is mandatory. Existing v2.5 Tier-2 implementation is unchanged. The v2.6 monitor reports failed separately; the legacy series_labels schema remains labelled/censored only.

## 4. pubDate-independent publication lineage

The v24 raw archive still preserves every version, including every pubDate change. Candidate admission uses earliest observed source plus canonical HTTPS article URL, independent of pubDate. The atomic new series_story_claims table prevents repeat forecasts on one article, while the original semantic cluster uniqueness retains same-title+time cross-source deduplication. URL tracking parameters are ignored; different canonical URLs are distinct and GUID-based lineage across moved URLs is a known residual risk.

## Required release verification

* The four negative tests: direct SQL mutation, corrupted raw evidence, late-success/error terminal outcomes, duplicate RSS pubDate.
* Positive tests: legitimate seal, correct hourly/entry/exit witness, valid timely closed candle, different URL, independent source.
* All module selftests and three-symbol offline E2E, full original Python/Node suites, crash/retry/idempotence tests.
* Verify exact deployed files and systemd ExecStart independently before any deployment.
* No model calls or activating the separate research-series gates as part of this remediation. The study's 100 events and 300 forecast arms are sample-size design, not DeepSeek service limitations.

The branch contains authored source changes pending independent MT validation. Do NOT claim production-ready until evidence has been returned and reviewed.

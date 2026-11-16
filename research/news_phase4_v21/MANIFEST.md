# Manifest — NEWS Phase 4 v2.1 audit bundle

Frozen at packaging time; file-level SHA-256 in `MANIFEST.sha256`.

## Provenance

| item | value |
|---|---|
| source repository | private `S960b/agent-dialogs`, branch `feature/news-v21-free-source-ingestion-20261115` |
| source commit at packaging | `878f3b18542e6df2aeb6cfa2a997c590de7c8a58` |
| installed deployment version | same commit; every file in `source/` verified byte-identical to its remote blob |
| runtime | MultiTool desktop `1.0.20261019` (AppImage), native SDK plugin API |
| provider / model observed | `deepseek` / `deepseek-v4-flash` |
| data as-of | see `taken_ms` inside `data/export_*.json` |
| tooling | Python 3.14.8, Node 22.23.1 |

## Audit-grade status per pair

| event | cohort | agent | tools | status |
|---|---|---|---|---|
| `e0c7b377a018` | context_news | null | 1 | **PILOT_DISCOVERY** — tools were available, information set not sealed |
| `40d21dd674c8` | context_news | null | 3 | refused (pilot, parser strictness) — no record |
| `d619c25574da` | context_news | null | 1 | refused (pilot, truncated answer) — no record |
| `f89bc542ffdd` | context_news | `news-restricted` | **0** | **PROSPECTIVE** — first audit-grade pair |

Only rows marked PROSPECTIVE may enter the evaluation cohort.

## Known limitations — read before judging

1. **n is tiny.** Two sealed pairs; one is audit-grade. The protocol's floor for
   any directional claim is 200 independent events. Nothing in this bundle
   supports a statement about profitability.
2. **Refusal rate is high in the pilot** (2 of 4 attempts). Causes are recorded:
   parser strictness (fixed) and a truncated model answer (not fixed — see
   below). The protocol's stop gate is >5% refusals in a prospective cohort; the
   pilot is far above that, which is exactly why the pilot is quarantined.
3. **Truncation is unexplained.** One arm returned a decision object with an
   unclosed string and brace. Response metadata carries no finish reason, so the
   cause cannot be attributed from public data alone; the run was refused, not
   repaired and not replayed.
4. **Provider quota is not observable.** No provider block in config, no account
   rows. The 20-call/10-pair cap is our own throttle, not a service limit, and
   "unlimited access" is not evidence of zero cost. Costs stated here are what
   the gateway reported.
5. **Reproduction is partial.** Market data is public and reproducible to the
   cent. Model outputs are not: the same prompt may yield a different answer, so
   an auditor can verify *consistency* of the recorded artifacts (hashes, timing,
   accounting) but cannot regenerate the exact forecasts.
6. **Withheld data.** No gateway database (contains unrelated private
   conversations), no full article texts (copyright), no prompts verbatim (only
   their hashes), no credentials. Nothing else is withheld.
7. **Selection is not random** — it is a deterministic rule over collected items.
   That is a design choice, and it bounds what any statistical claim can mean.

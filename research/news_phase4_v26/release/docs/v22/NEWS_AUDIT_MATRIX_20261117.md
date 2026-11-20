# NEWS audit matrix — v2.1 D01–D19 and the v2.2/v2.4 additions

**Dated:** 2026-11-17 (UTC). Columns mean exactly what they say:

- **fixed** — the defect is corrected in the public source.
- **tested** — a regression test fails without the fix and passes with it.
- **loaded** — whether the RUNNING process executes this code. **No stage is
  loaded**: the GUI process started before the first fix.

**"Not loaded" is not "not running".** The distinction this matrix previously
collapsed:

| what | state | evidence |
|---|---|---|
| legacy v2.1 collector / label lane | **RUNNING** | `news-v21-plugin.log` written every 30–60 s, last line `T03:15:59Z`; 6 production records, 6/6 labelled |
| legacy Phase-4 v2 collector | **RUNNING** | `news-p4-v2-plugin.log` last written `T06:14:56Z` (local) |
| old GUI host process | **RUNNING** | pid 3391340, started 2026-11-16 17:04 |
| every fix from stages 1–2C and v2.2/v2.4 | **NOT LOADED** | loaded build predates them |
| pilot settle hook | **NOT LOADED** | `v21_pilot_settle` = 0 lines in the log |
| model inference | **PAUSED** | `PAUSED_MODEL_INFERENCE` flag present, 225 bytes, 2026-11-16 16:43 |
| prospective v2.2 / v2.4 | **ZERO** | no data, no counter |

So: the legacy experiment is alive and producing labels, the new code is not
executing anywhere, and inference is paused. Saying "nothing is running" would
be wrong.
- **status** — what an auditor may conclude today.

Nobody should read "fixed + tested" as "in production". Column 3 is empty for
every row, and that is the honest state of this experiment.

## v2.1 findings

| ID | fixed | tested | loaded | status |
|---|---|---|---|---|
| D01 unknown tool count → 0 | yes | yes (18 tests) | no | closed in source |
| D02 tool-history error → 0 | yes | yes (5 fixtures) | no | closed in source |
| D03 403/`forbidden` treated as boundary | yes | yes (11 tests) | no | closed in source; isolation still unproven, cohort NO-GO |
| D04 no market-only arm / no analyzer | **partial** | yes | no | v2.2 adds the M arm and an analyzer; no live data |
| D05 media → primary in prompt | no change needed | yes | no | contradicted by live probe (`secondary_media`) |
| D06 declared vs effective cohort | yes | yes | no | closed in source |
| D07 300-char probe truncation | yes | yes | no | closed in source |
| D08 no durable pilot attempt ledger | yes | yes (12 tests) | no | closed in source |
| D09 dedupe before ranking | yes | yes (3 tests) | no | closed in source |
| D10 `origin_url` dropped from export | yes | yes (4 tests) | no | closed in source, 113/113 live events carry a URL |
| D11 entry before arm completion | yes | yes | no | closed in source |
| D12 no writer-side arm contract | yes | yes (8 tests) | no | closed in source |
| D13 monetary stop in public source | yes | yes | no | closed in source |
| D14 two divergent implementations | **partial** | yes | no | `v21_cycle.py` now byte-identical; flat `v21_export.py` still a stale duplicate — **owner decision** |
| D15 candle evidence | yes | yes | no | closed in source |
| D16 synthetic lease shares production | yes | yes (7 tests + rehearsal) | no | **live migration not run — owner gate** |
| D17 bool numerics / duplicate JSON keys | yes | yes (5 tests) | no | closed in source |
| D18 unknown attempt reported as refused | yes | yes (4 tests) | no | closed in source |
| D19 environment-dependent tests | yes | yes | no | closed for the two known fixtures; no exhaustive audit claimed |

## v2.2 three-arm additions

| item | fixed | tested | loaded | status |
|---|---|---|---|---|
| M arm built from the market dict, not filtered from news | yes | yes (6 tests) | no | contract proven offline |
| Isolation audit covers header, metadata, ordering, look-ahead | yes | yes | no | offline only |
| Durable three-arm attempts before invocation | yes | yes (12 tests) | no | offline only |
| Three-arm ledger, seal, export | yes | yes | no | offline only |
| Brier vs climatology separated; FLAT in PnL only | yes | yes (5 tests) | no | methodology corrected pre-data |
| Paired sign-flip p-value; Holm on p-values | yes | yes | no | offline only |
| Overlap measured, non-overlapping sensitivity | yes | yes (3 tests) | no | offline only |
| **Any predictive evidence** | — | — | — | **none. No v2.2 data exists.** |

## v2.4 source-coverage additions

| item | fixed | tested | loaded | status |
|---|---|---|---|---|
| Frozen coverage grid; missing ≠ quiet | yes | yes | no | offline only |
| Probe schedule anchored to a declared start + cadence + grace | yes | yes (11 tests) | no | offline only |
| Story coverage and availability computed separately | yes | yes | no | offline only |
| Six availability states incl. `intermittent` / `stale` | yes | yes | no | offline only |
| Duplicates rejected as duplicates, naming the winner | yes | yes | no | offline only |
| Registry isolation (no process globals) | yes | yes | no | offline only |
| Heartbeat producer contract (offline, no network) | yes | yes (13 tests) | no | interface only |
| **Authenticity / provenance** | **no** | — | — | `content_sha256` is a checksum, not a signature |
| **Live adapters / feeds** | **no** | — | — | no network code exists, by design |

## Readiness summary

| question | answer |
|---|---|
| Is the **code** auditable? | **Yes, scoped to the offline fixture contract** — 434 deterministic tests; nothing is claimed about forecasting validity |
| Is the **methodology** auditable? | **Yes, scoped** — pre-registered, amendments committed before any data (commit timestamps prove freshness, not priority) |
| Is source **authenticity** proven? | **No** — checksums are not signatures |
| Is source **licensing** settled? | **No** — no licence review exists |
| Is anything **running**? | **Yes** — the legacy v2.1 and v2 collectors and their label lanes; see the table above |
| Is any **fix loaded**? | **No** — the running process predates every one of them |
| Is there any **evidence of profit**? | **No** — and none is claimed |
| Is the prospective cohort live? | **No** — PROSPECTIVE = 0 |

## Owner approvals required before anything becomes live

1. **Plugin reload** — the GUI must restart to load any of this. It also unblocks
   the overdue pilot `8f778a0b2180` (due 19:45:32Z on 2026-11-16).
2. **Pilot settle** — either the reload above or an explicit authorization to run
   `v21_pilot_cycle.py settle` once. Expected outcome: `label_drift_too_large`
   and an honest unlabelled record, not a fabricated price.
3. **D16 live migration** — `dispatch.sqlite` still has `CHECK(id = 1)`; the
   rehearsed procedure must be run with a backup, or the writer will reject the
   synthetic bucket.
4. **D14 duplicate removal** — deleting the stale flat `v21_export.py` is a
   deployment decision, not a test decision.
5. **Network adapters and data collection** — needs a licensed source list, a
   dated freeze, a real availability probe, and signed or allowlisted origins.
   None of that exists.

Until each of these is granted in writing, every claim in this repository is
about source code and offline fixtures only.

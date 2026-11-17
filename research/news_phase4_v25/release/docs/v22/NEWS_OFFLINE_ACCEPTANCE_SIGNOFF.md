# NEWS v2.4 — independent offline acceptance, signed off by agent

**Dated:** 2026-11-17 (UTC).
**Audited commit:** `443fa3e21b9e1ef5c99db32c95077ccadd8b577b` (`main`).
**Manifest of the audited tree:** `da3fe4bcb4cb06df7901b53aefecd07ba572bae95f07e2e740b7ceff2202537b`
(`research/news_phase4_v21/MANIFEST.sha256`).

**Objective scope.** This document records what an offline, read-only pass over
the pinned commit actually established. It is **not** a claim of forecasting
validity, data quality, source authenticity, licensing, or profitability.

**Project status: offline audit candidate. NOT production ready.**

## 1. Environment and exact results

| step | command | exit | result |
|---|---|---|---|
| clone | `git clone git@github.com:S960b/S960b.git` | **0** | — |
| manifest | `sha256sum -c MANIFEST.sha256` (before tests) | **0** | clean |
| Python | `env -i HOME=<tmp> PATH=/usr/bin:/bin PYTHONPATH=source /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py'` | **0** | 434 tests OK (2 skipped), Python 3.14.8 |
| Node | `env -i ... node --test tests/test_v21_paper_runner.mjs tests/test_v21_service.mjs` | **0 / 0** | 23 + 4 pass |
| acceptance | `source/v24_acceptance.py` | **0** | balances true/true, deduplicated 1 |

**Independently reproducible: yes**, from a fresh clone, with no network beyond
the clone itself and no database outside a temporary file.

## 2. Manifest vs tracked files

```
manifest entries: 96      git-tracked files: 97
in manifest, not tracked: (none)
tracked, not in manifest: MANIFEST.sha256  (it cannot hash itself)
```

No untracked critical input exists: every source, fixture, test and document the
suites read is tracked and hashed. The manifest is deliberately generated over
tracked files only, so `sha256sum -c` is meaningful **before** any test runs.

## 3. Pre-registration dating — what the evidence actually shows

| document | date inside it | first commit containing it |
|---|---|---|
| `NEWS_V22_PREREGISTRATION.md` | 2026-11-16 | 2026-11-17 00:22 +0300 |
| `NEWS_V22_AMENDMENT_2.md` | 2026-11-17 | 2026-11-17 00:22 +0300 |
| `NEWS_V24_SOURCE_PROTOCOL.md` | 2026-11-17 | 2026-11-17 01:14 +0300 |
| `NEWS_V24_AMENDMENT_1.md` | 2026-11-17 | 2026-11-17 02:15 +0300 |

**Conclusion, stated narrowly:** every document entered Git before any v2.2 or
v2.4 record existed, which is the property that matters. Commit timestamps prove
*freshness relative to data*, not *priority between drafts*. Any claim that a
dated document was "written earlier than a reviewer said" is unsupported and is
not made here.

## 4. No data, no inference

- No `v22_*` table exists in any live database (`v21`, `paper`, `pilot`,
  `dispatch`, `bluesky` were each inspected: zero such tables).
- `PAUSED_MODEL_INFERENCE` present.
- Prospective v2.2 and v2.4 counts: **0**.
- Production v2.1 ledger untouched: 6 records, 6/6 labels settled.

## 5. Test integrity — mutation check

A regression was introduced on purpose (a failed probe made to count as an
answered one) and the suite was re-run:

```
mutant: FAILED (failures=3)  exit=1
  test_one_failed_probe_in_thirty_is_not_healthy
  test_failed_probe_at_the_last_instant_is_stale
  test_all_failed_is_failed
```

The adversarial fixtures are therefore not self-confirming: they fail when the
behaviour regresses. **Caveat found during this audit:** if `python3` resolves to
the desktop AppImage rather than `/usr/bin/python3`, a run can exit 0 while
doing nothing. That is an environment hazard, not a test defect, and the handoff
now says so explicitly.

No test opens a socket, calls a model, or touches a live database — verified by
grep across `source/v22_*`, `source/v24_*` and their test files.

## 6. Three-arm analysis — completeness, and what it is not

Verified directly: an event with no outcome yields zero analysable rows; a
two-arm record yields zero; a complete triple yields exactly one. There is no
imputation and no partial pairing.

**Survivor bias, stated plainly:** the analysable set is limited to events that
sealed *and* matured. That set is not a random sample of events, and no result
computed on it generalises beyond "events that survived to a label". The
protocol discloses this; this document repeats it so it cannot be missed.

## 7. Unresolved, with owners

| item | state | owner action |
|---|---|---|
| **D04** | partial — M arm and analyzer offline only | none until live data is allowed |
| **D14** | partial — flat `v21_export.py` still stale | owner decision to delete |
| **D16** | migration rehearsed, **not run** | owner authorizes downtime + backup |
| **D19** | two known fixtures fixed | no exhaustive claim |
| **Provenance** | **NOT PROVEN** — checksums ≠ signatures | signed/allowlisted origin, raw payload digest |
| **Licensing** | **NOT PROVEN** | per-source licence review |
| **Network reliability** | unmeasured — no probe, no feed | out of scope until approved |

## 8. Handoff to a human external reviewer

1. Clone the pinned commit; verify the manifest **before** running anything.
2. Use `/usr/bin/python3` explicitly, and `env -i` with a throwaway HOME so your
   own data cannot leak into a run.
3. Read, in this order: this document, `NEWS_AUDIT_MATRIX_20261117.md`,
   `NEWS_AUDITOR_HANDOFF.md`, then the pre-registrations.
4. Check claims against source, not against summaries. Every number here has a
   command behind it.
5. Anything about live behaviour, profit or source trust must be treated as
   unproven until the owner gates in section 7 are cleared.

## 9. Verdicts

| question | verdict |
|---|---|
| Offline code + fixture contract | **GO (scoped)** |
| Methodology documentation | **GO (scoped)** |
| Source authenticity / licensing | **NO-GO** |
| Live collection | **NO-GO** |
| Model inference | **NO-GO** (paused) |
| Profitability | **unknown, unclaimed** |
| Production readiness | **NOT READY** |

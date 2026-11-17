# NEWS v2.4 — auditor handoff

**Dated:** 2026-11-17 (UTC). Pinned public commit: see the report that ships with
this file; the SHA manifest is `research/news_phase4_v21/MANIFEST.sha256` and is
generated over **tracked files only** (bytecode excluded) so that
`sha256sum -c` is meaningful in a fresh clone **before** any test runs.

## 1. Reproduce everything, offline

```bash
# Step 1 needs Git network (clone). Everything after it is fully offline.
git clone git@github.com:S960b/S960b.git && cd S960b/research/news_phase4_v21

# Step 2 onwards: no network at all.
sha256sum -c MANIFEST.sha256            # verifies source, fixtures, docs
PYTHONPATH=source python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_v21_paper_runner.mjs tests/test_v21_service.mjs
python3 source/v24_acceptance.py        # offline acceptance report (JSON)
```

**Be precise about "offline":** the clone is a network operation; test
execution is not. No test opens a socket, calls a model, or touches a database
outside a temporary file. If a test needs anything else, that is a bug, not a
prerequisite.

**Use `/usr/bin/python3`, not `python3` from `PATH`, on this host.** The desktop
AppImage can shadow `python3` and then a test run silently does nothing while
still exiting 0 — observed during this audit, and the reason the mutation check
below had to be repeated.

## 2. What is fixed, tested and loaded

Every finding, with pointers, is in `NEWS_AUDIT_MATRIX_20261117.md`. The single
sentence that matters: **everything is fixed and tested in source, and nothing is
loaded in the running process.** The legacy collectors and label lanes are
running; the fixes are not executing anywhere.

## 3. Dated pre-registration versions

| version | file | state |
|---|---|---|
| v2.1 prospective | `docs/NEWS_V21_AUDIT_PROTOCOL.md` (+ dated addendum) | frozen, untouched |
| v2.2 three-arm | `docs/v22/NEWS_V22_PREREGISTRATION.md` | pre-data, amended once |
| v2.2 amendment #2 | `docs/v22/NEWS_V22_AMENDMENT_2.md` | pre-data |
| v2.4 sources | `docs/v22/NEWS_V24_SOURCE_PROTOCOL.md` | pre-data |
| v2.4 amendment #1 | `docs/v22/NEWS_V24_AMENDMENT_1.md` | pre-data |

No amendment was written after seeing data, because no v2.2 or v2.4 data exists.

## 4. Unresolved items

| ID | state | why it is unresolved |
|---|---|---|
| **D04** | partial | the M arm and an analyzer exist offline; no data, no baselines run |
| **D14** | partial | `v21_cycle.py` is byte-identical public ↔ disk; the flat `v21_export.py` is still a stale duplicate — deletion is a deployment decision |
| **D16** | fixed in source, **not migrated** | `dispatch.sqlite` still carries `CHECK(id = 1)`; the rehearsed migration has not been run |
| **D19** | partial | the two known host-dependent fixtures are fixed; no exhaustive audit of every test is claimed |
| **Provenance** | **NOT PROVEN** | `content_sha256` is a checksum over `title\|published_ms`. It proves integrity in transit, never origin. A forged record carries a valid checksum |
| **Network reliability** | unmeasured | there is no probe, no feed and no network code in the repository |

## 5. Trust boundary, stated once

The probe records what an observer *claims* it saw. `Heartbeat.ok` is an
assertion by the producer process. Real authenticity needs a locally-run probe
against an allowlisted origin, a signed attestation, raw response retention and
a digest chain anchored outside the producer. None of that exists, and no
document in this repository may imply otherwise.

## 6. Operator checklist — **NOT AUTHORIZED**

Every step below requires an explicit written approval that has **not** been
given. This list exists so an operator can see the blast radius before asking.

**Rollout (not authorized)**
1. Stop the writer that opens `dispatch.sqlite`.
2. `sqlite3 dispatch.sqlite ".backup dispatch.pre-d16-$(date +%s).sqlite"`.
3. Run the rehearsed lease migration (`v21_dispatch.open_db`), then verify
   `CHECK(id IN (0, 1))` and that the previous holder is still present.
4. Restart the GUI so the loaded plugin matches the source.
5. Verify `v21_pilot_settle` appears in the plugin log within one tick.

**Rollback (not authorized) — and NOT lossless**
1. Stop the GUI **and every writer** first. Restoring over a database that is
   still open discards any write made after the backup.
2. Restore `dispatch.sqlite` from the backup taken in rollout step 2.
3. `git -C <deploy> checkout <previous commit>` and restart.
4. Confirm `v21_pilot_settle` count returns to 0 and the label lane still writes.

Rollback restores the schema, **not the data written since the backup**: rows
created after that instant are lost and the operator must decide, in advance,
whether that loss is acceptable. There is no lossless rollback of a live ledger
without a continuous WAL archive, which does not exist here.

**Also not authorized, separately:** settling pilot `8f778a0b2180` (overdue
since 2026-11-16T19:45:32Z), removing the flat `v21_export.py`, and any network
adapter or live data collection.

## 7. Independent verdicts

| question | verdict |
|---|---|
| Code audit-ready? | **GO, SCOPED** — offline fixture contract only: deterministic, 434 tests. Not a statement about forecasting validity |
| Methodology audit-ready? | **GO, SCOPED** — pre-registered and amended before data; the amendments were committed the same night they are dated, so commit timestamps prove freshness, not priority |
| Source authenticity | **NOT PROVEN** — checksums, not signatures |
| Source licensing | **NOT PROVEN** — no licence review exists |
| Live collection? | **NO-GO** — no licensed sources, no probe, no authenticity |
| Inference? | **NO-GO** — `PAUSED_MODEL_INFERENCE` stands; prospective = 0 |
| Profitability? | **Unknown and unclaimed** — mock arms are plumbing, not evidence |

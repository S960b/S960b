# P36.2 — RELEASE CLOSED / ЗАКРЫТО (2026-11-08)

**Status:** deployed and smoke-verified on Kali. ChatGPT approved closure after the owner-confirmed desktop restart and a fresh isolated Git-dialogue round trip. This is an acceptance record, not a claim that every possible crash path has been reproduced on production.

## Provenance

- Repository: `S960b/agent-dialogs`
- Current release commit: [`b63116d35dc200db28b95393946f990e807c29dd`](https://github.com/S960b/agent-dialogs/commit/b63116d35dc200db28b95393946f990e807c29dd) (P36.2 + NEWS v1.3.1).
- Parent NEWS v1.3.1: `8c64491d4e83f7d69f14d79a7a68ed34c6fd99f7` (unchanged NEWS files after the bridge integration).
- Bridge source: `deploy/mt-git-inbox-p36.mjs`.
- On-disk installed plugin SHA-256 (MT verified): `f404c9bd184e5eab0f1277ed8ad3b509f179c5897feddce7c1bf036b7845e7dd`.
- Running bridge checkout verified by MT: `b63116d3`, clean detached checkout.
- Replaced P36.1 plugin SHA-256: `cf77d7e4a822981adb65b1fbf43ada5d4b4ebc07e3ed7cc847ffec49936ec7f8`.

## Verified implementation

- A failed/ambiguous MT SDK prompt can leave a durable `inflight` reservation even if the MT reply already exists in Git. P36.2 reads the validated immutable conversation without replaying the prompt: confirms the original seq/message_id and adjacent MT reply; then advances `seen` only up to the reserved request seq and clears the reservation.
- Failed reads, absent/incorrect reply, mismatched actor/id, oversized history and dry-run stay fail-closed. Rate-limit: at most one reconciliation check per task per five minutes. No elapsed-time-only clearance.
- Isolated integration at exact `b63116d3`: **1211 Python tests + 4 subtests passed**, **8/8 P36.2 Node**, **18/18 NEWS Node**, plugin smoke passed, compilation passed (MT report `mt_p362_news_v131_integration_report_20261108_v1`).
- Controlled installation finished: Taskboard on `127.0.0.1:8787` and bridge on `127.0.0.1:8765` active; expected unauthorized HTTP 401 from Taskboard and bridge /ping OK. Owner restarted MultiTool afterward.
- After installation, MT verified desktop PID start and `plugin loaded` log timestamp **after** new plugin file write. Smoke test in separate branch `dialogs/task_ebc5c5de5a385a73341a9b5ec4f3499e`: one `seq=1` ChatGPT task and one `seq=2` MT report with exact `reply_to`, status `waiting_for=chatgpt`. No duplicate reply observed. MT reported zero extraneous stale `inflight` reservations.

## Evidence links

- [Integration / test report (seq10)](https://github.com/S960b/agent-dialogs/blob/ed077cbb4fbf1d725f0734feb92b08c164dd0f86/messages/10.mt.json)
- [Private pre-deployment / backup report (seq12)](https://github.com/S960b/agent-dialogs/blob/5beb63aaa86f02fdeb162d337fe131f3910e41fb/messages/12.mt.json)
- [Actual installation report (seq14)](https://github.com/S960b/agent-dialogs/blob/655c83632c476e3ac930b948531a5e14d4652c8b/messages/14.mt.json)
- [Isolated end-to-end smoke MT reply (seq2)](https://github.com/S960b/agent-dialogs/blob/563937b366a754662b96e0552718ee0d335cdcf3/messages/2.mt.json)
- [P36.2 regression tests](../deploy/p362-inflight-tests.mjs) and [recovery specification](P362_INFLIGHT_RECOVERY.md).

## Backup, rollback and limitations

- MT saved a private pre-deployment backup at `/home/kali/agent-dialog-beta/backups/p362-predeploy-20261108-103802/` and a staged package at `/home/kali/agent-dialog-beta/backups/p362-deploy-package-20261108-103859/`. They are NOT stored in Git and must not be deleted automatically.
- Rollback to P36.1 requires a controlled app restart. **Never blindly replace the current inbox-state.json with an older backup** after new messages, since this may lose `seen` and cause duplicate delivery; reconcile latest state first.
- Live smoke proved a successful two-way message round-trip but **did not inject a real SDK failure**. The uncertain-delivery recovery mechanism was verified offline. No universal exactly-once guarantee for arbitrary external side effects.
- Future hardening (out of this completed release): explicit reply message_id test, improved operator diagnostics and long-horizon observation. Keep existing GitHub Issue #3 open as future backlog; Issue #4 is separate monitoring and should not be implicitly closed by this release.
- NEWS Intelligence v1.3.1 was kept byte-identical through the bridge change. New NEWS Phase 3 work belongs in its own task/branch, not as a change to this release.

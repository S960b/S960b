# P35 — Taskboard column «Последнее действие»

**Deployed 2026-11-04** to `/home/kali/agent-dialog-beta/code/src/git_dialog/`, from verified source commit `558a759a525646d502bf1cdb25d7983087df7230`. The installed bytes were subsequently committed as `edca980ec8dddff00d625fff444a4d9a9ffd61c6` in `feature/p35-taskboard-installed`.

Files installed (sha256):
- `taskboard.py`: `516dd733a8596b66b1186de44bf6910e06a307f5679536e01be7efa56064e0d9`
- `taskboard.js`: `aa6716e01c4f576e9ceb3705451366395ade598b168e16e4d9fef1e53aa1fe8a`
- `taskboard.html`: `832034d4e7e2abdfc74be0bb152f16ad47f55ec099bf86e88d886c7a985d9a34`

`taskboard.css` remains unchanged. Backup: `/home/kali/.local/backups/p35-pre-20261104_165627`.

Tools:
- `stage_last_action.py`: writes *only* three patched files into a new, empty staging directory, with pinned original-source SHA checks. **It is not an idempotent deployer** and must not run against already patched originals.
- `check_staged.py`: offline Python/WSGI tests. Expects a complete isolated package rooted at `<test_dir>/git_dialog/`, including unchanged siblings and `taskboard.css`. The stager writes three files flat: place these staged files under `git_dialog/` in a separate package copy before running the check.
- `check_js.cjs`: offline JS classification test.

Last MT report: `P35_STAGED_OK`, `P35_OFFLINE_PASS`, `P35_JS_PASS`, plus 19/19 existing P32 Taskboard tests.

UI semantics: `waiting_for=mt` does not mean work is in progress. Git history proves Git writes and MT reports. Only local MT inbox `seen_seq` proves dispatch; `inflight` is an **unconfirmed** delivery attempt. The local state is read-only and sanitized to `source/seen_seq/inflight_seq`. No additional GitHub polling, timers, buttons or stored credentials were introduced.

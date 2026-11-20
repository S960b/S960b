# NEWS v2.6 — RSS-only canary + prospective classifier (agent runbook)

**Owner direction, 20 Nov 2026:** continue implementation; do not change the
existing v2.5 collector, DB or timer; do not invoke the model or place orders.

## Inputs

- Branch: `news-v25-next100-chatgpt-20261119`
- Pin: **`9475ac2bd7fd1f40df3d28288134ebea072828b4`** (or newer explicitly
  approved SHA after test fixes).
- Code:
  - `series100/v26_series_runner.py --poll`: fetches 14 public RSS/Atom
    sources, writes only the independent v2.6 SQLite DB/archive.
  - `series100/v26_rss_arrival_audit.py --baseline/--report`
  - `series100/v26_arrival_candidate_probe.py --selftest` and standard mode
  - `series100/systemd/news-v26-rss-canary.{service,timer}.in`.

## Stage 1. Offline, never model calls

1. Fetch exact commit into separate clean checkout. Set `CODE_DIR` to the
   verified absolute `research/news_phase4_v25/series100` path.
2. `python3 -m py_compile "$CODE_DIR/v26_arrival_candidate_probe.py"`
3. `python3 "$CODE_DIR/v26_arrival_candidate_probe.py" --selftest`
4. Re-run `v26_series_runner.py --selftest` and
   `v26_monitor.py --selftest`. Stop on ANY failure.
5. Read the existing ephemeral canary by using the independent
   `/tmp/v26canary/run-20261120T0000/{rss.sqlite,rss_meta.sqlite,arrival_baseline.json}`
   paths with `v26_arrival_candidate_probe.py`. Check whether the new
   CoinDesk Anthropic/Ark Invest publication qualifies as BTC/ETH/SOL.
   Do not misreport an on-time headline as a market signal without classification.

## Stage 2. New persistent RSS baseline

1. Confirm no old v2.5 state is referenced. The v2.6 state root is
   `~/.local/share/news-phase4-v26-series100` only.
2. Run `python3 "$CODE_DIR/v26_series_runner.py" --poll` **once**.
3. Immediately create a write-once baseline:
   `python3 "$CODE_DIR/v26_rss_arrival_audit.py" --baseline --feeds-db
   "$HOME/.local/share/news-phase4-v26-series100/rss.sqlite" --metadata-db
   "$HOME/.local/share/news-phase4-v26-series100/rss_meta.sqlite"
   --baseline-file "$HOME/.local/share/news-phase4-v26-series100/arrival_baseline.json"`.
   If the baseline already exists, do NOT overwrite or regenerate it.
4. Confirm the baseline fingerprint and retained raw RSS files.

## Stage 3. Only user-systemd RSS-only timer

1. Check `systemctl --user` works. No restart of the agent or Git bridge.
2. Render the two `.in` templates into
   `~/.config/systemd/user/news-v26-rss-canary.service` and `.timer`,
   replacing `@PYTHON@` with the verified absolute Python 3 path and
   `@RUNNER@` with the verified absolute `v26_series_runner.py` path.
3. Read the rendered unit before enabling. **ExecStart must end in
   `--poll`, never `--execute`.** Verify there is no activation or gate.
4. `systemctl --user daemon-reload`, then
   `systemctl --user enable --now news-v26-rss-canary.timer`.
5. Confirm the timer fires and the service completes, without overlapping
   instances. Inspect `journalctl --user -u news-v26-rss-canary.service`.
6. After two ticks, run arrival `--report` and candidate probe with the same
   persistent baseline/DBs. Track `new_published_after_baseline`,
   `publication_lag_ms`, and **material and Tier-2-admissible** counts by
   source. On 429/403, log the failure; no circumvention.

## Acceptance and stop conditions

- Existing v2.5 collector/monitor and series unchanged.
- No source writeback, synthetic timestamps or manual backfill.
- New raw RSS archive + sidecar + baseline consistent, untouched after creation.
- Zero active v2.6 model-execution timer; v2.6 `--execute` not called.
- If Python tests, systemd-user, or RSS source integrity fail, STOP and report.
- Report only verified facts, including actual paths/timestamps/unit status.

**Purpose:** test real prospective RSS transport latency and event relevance
over multiple future publications, not relax 120-second crypto rules based
on the historical 937-item initial snapshot.

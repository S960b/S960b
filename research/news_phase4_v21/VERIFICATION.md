# Verification log — NEWS v2.1 audit bundle

Every command below was run against a **fresh clone of the public repository**,
not against a personal working directory. No test touches the network, a model,
or a production service.

**Tested checkout:** `9cbface57d1ff7d19085676d885582b729b5a84a` (the commit this
document ships with is the one immediately following it; the content under test
is identical except for documentation).

## Python

```bash
cd research/news_phase4_v21
PYTHONPATH=source python3 -m unittest discover -s tests -p 'test_v21_*.py'
```
```
Ran 199 tests in 1.699s
OK
```

`PYTHONPATH=source` is required: the production modules live in `source/` and the
tests import them the way the runtime does.

## Node

```bash
cd research/news_phase4_v21/tests
HOME=$(mktemp -d) node --test test_v21_paper_runner.mjs test_v21_service.mjs probe_test.mjs
```
```
# tests 24
# pass 24
# fail 0
```

A throwaway `HOME` is used on purpose: the suite must not depend on any machine
that happens to exist on this host.

- `test_v21_paper_runner.mjs` imports `../source/v21_paper_runner.mjs`.
- `test_v21_service.mjs` reads `../source/v21_service.mjs`, the **disabled
  historical supervisor** (no model client; kept only so its tests can run).
- `probe_test.mjs` imports `../source/v21_plugin.mjs` and drives it with a mock SDK
  client inside `mkdtempSync()` directories.

`source/news-p4-v2-helpers.mjs` is shipped because the runner and plugin import
it; there are no absolute machine paths in `source/`, `tests/` or `scripts/`.

## Manifest

```bash
sha256sum -c MANIFEST.sha256
```
**61 files checked, 0 failures.** The manifest covers every file in the bundle
including `AUDITOR_PROMPT_RU.md` and excludes only itself.

`AUDITOR_PROMPT_RU.md` sha256
`0af4ea849c9fbb8ac0007f96eb9ae12417b566316e43060b9a1208c3f0853a62`
equals the blob at commit `a2112b463cb895d5fd4bea757856bdee6d9137c7`. It is not
modified by this work.

## Export reproducibility

```bash
PYTHONPATH=source python3 scripts/v21_export.py \
  --data-dir <dir with v21.sqlite, paper.sqlite, dispatch.sqlite> \
  --gateway-db <opencode.db, optional> --out export.json
PYTHONPATH=source python3 scripts/v21_pilot_export.py --out pilot_sidecar.json
```

Both are read-only (`PRAGMA query_only=ON`) and their shapes are asserted by
tests. `gateway_usage` is reported as `unavailable` when the gateway database is
absent — it is never guessed.

Relevant current exports: `data/export_20261116T1600Z.json` (production, 6
records, 6 settled outcomes) and `data/pilot_sidecar_20261116T1600Z.json` (two
pilot pairs: one `PILOT_ANCHOR_INVALID`, one `ANCHORED`).

## What these tests do not prove

Passing tests prove the code behaves as specified offline. They say nothing about
predictive value, and nothing about tool isolation, which is **not established**
on this runtime.

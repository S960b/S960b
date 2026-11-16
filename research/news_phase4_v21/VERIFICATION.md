# Verification log — NEWS v2.1 audit bundle

Every command below was run against a **fresh clone of the public repository**,
not against the developer's working directory. No network access, no model call,
no production service is touched by any of them.

Clone used: `git clone --depth 2 git@github.com:S960b/S960b.git`

## Python

```bash
cd research/news_phase4_v21
PYTHONPATH=source /usr/bin/python3 -m unittest discover -s tests -p 'test_v21_*.py'
```
```
Ran 167 tests in 0.335s
OK
```

`PYTHONPATH=source` is required: the production modules live in `source/`, and the
tests import them the way the runtime does.

## Node

```bash
cd research/news_phase4_v21/tests
node --test test_v21_paper_runner.mjs test_v21_service.mjs probe_test.mjs
```
```
# tests 22
# pass 22
# fail 0
```

These three files are the complete Node surface.

- `test_v21_paper_runner.mjs` → imports `../source/v21_paper_runner.mjs`.
- `test_v21_service.mjs` → reads `../source/v21_service.mjs`. That module is the
  **disabled historical supervisor**: it runs the cycle without any model client
  and is kept here only so its tests can run. It is **not** production.
- `probe_test.mjs` → imports `../source/v21_plugin.mjs` and drives it with a mock
  SDK client inside `mkdtempSync()` directories. No model is called.

`source/news-p4-v2-helpers.mjs` is included because the runner and the plugin
both import it; in the runtime it lives in the v2 directory, here it is a
sibling so the bundle is self-contained.

## Manifest

```bash
sha256sum -c MANIFEST.sha256
```
60 files checked, 0 failures. `MANIFEST.sha256` covers **every** file in the
bundle including `AUDITOR_PROMPT_RU.md`, and excludes only itself.

`AUDITOR_PROMPT_RU.md` sha256
`0af4ea849c9fbb8ac0007f96eb9ae12417b566316e43060b9a1208c3f0853a62`
equals the blob at the commit that introduced it, `a2112b463cb895d5fd4bea757856bdee6d9137c7`.
It is not modified by this work.

## Export reproducibility

```bash
cd research/news_phase4_v21
PYTHONPATH=source /usr/bin/python3 scripts/v21_export.py \
  --data-dir <dir with v21.sqlite, paper.sqlite, dispatch.sqlite> \
  --gateway-db <path to opencode.db, optional> \
  --out export.json
```

The exporter is read-only (`PRAGMA query_only=ON`) and its output shape is
asserted by `tests/test_v21_export_schema.py`, which builds a synthetic store and
runs the real CLI. `gateway_usage` is reported as `unavailable` when the gateway
database is absent — it is never guessed.

Note on `data/export_20261116.json`: that file was produced by this same
exporter at the time, before the exporter itself was added to the bundle. It is
kept byte-identical as the historical snapshot; the new dated export
`data/export_20261116T1335Z.json` supersedes it and adds
`attempt_reconciliation`.

## What these tests do not prove

Passing tests prove the code behaves as specified offline. They say nothing about
predictive value: that requires prospective events under the sealed information
set, and the protocol's floor is 200 independent events.

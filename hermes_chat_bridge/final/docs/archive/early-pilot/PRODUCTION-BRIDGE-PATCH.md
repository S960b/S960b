# Candidate-only live-bridge wiring patch

Input is the exact working source reported by MT, SHA256:
`ffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f`.
It is not Git HEAD; its eight unrelated local hunks must survive.

```
PYTHONPATH=src python -m git_dialog.bridge_patch --source KNOWN_BRIDGE_SOURCE --output NEW_CANDIDATE_FILE
```

The tool reads that one explicit regular non-symlink source file (max512KiB),
checks exact hash, parses AST without importing/executing the file, then makes
four line-preserving edits. It never searches directories or reads runtime
config, environment, credentials, DB or histories. A changed source/ambiguous
AST shape/already-patched input is rejected. Output must be a new O_EXCL/600
file, never the original or an existing candidate. It prints hashes/status,
not the code or configuration. A failed output write may leave a partial new
candidate; it never deletes or overwrites it automatically.

Edits: default `BridgeApp._beta_forwarder=None`; trusted local
`enable_beta_wakeup(state_dir, port=8790)` method loads the dedicated candidate
key and creates one reviewed sender; metadata-only offer after payload assembly
before the original subscription loop; start immediately before original ASGI
lifespan entry; stop in lifespan finally, with original pilot_lock.close kept
in its own finally even if stop fails. Original subscription/delivery/OAuth
logic is not replaced. Default-off makes no new thread, network or key read.

Enabling must occur through trusted startup code before lifespan entry, never
through a message body/public endpoint. This command only prepares the source
candidate; it does not supply that startup configuration, start/restart bridge,
provision a key, replace P1 or modify any running service. A reviewed bootstrap
or explicit existing launcher change remains necessary to actually enable it.

MT must first generate this candidate from the pinned dirty file in a new
isolated location, return source-before/source-after/candidate hashes and safe
diff confirmation, compile it without execution, and leave the live file alone.
Changed input means stop, not rebase/repair or use Git HEAD. Local AST fixture
tests do not prove transformation of the actual 1237-line file; actual candidate
generation is a separate acceptance. Live enablement/deployment and real Git
network acceptance remain unproven until their factual reports.

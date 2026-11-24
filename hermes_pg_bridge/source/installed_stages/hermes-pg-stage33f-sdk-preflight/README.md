# Stage 33F-A: bounded, live-capable MultiTool SDK candidate — NO cutover

The previous Stage 33E candidate is **forced dry-run**. Do NOT simply load
another plugin into the existing MultiTool process: that can create two
concurrent consumers for the same canary dialogue and duplicate SDK work.

This package prepares, but NEVER registers or runs, a Stage 33F agent-side
candidate using the already verified `bridge_agent` Postgres identity. The
candidate has:

- exactly one hard-coded task id, the existing completed canary;
- one future test task message (`pgv2-stage33f-sdk-task-v1`, seq 5) and report
  (`pgv2-stage33f-sdk-report-v1`, seq 6);
- exact permitted response `PGV2_ROLE_BOUND_MT_SDK_ACK_20261122`;
- a separate durable inbox state directory;
- only `bridge_agent` SQL actor wrappers (no `bridge_app`);
- the original prompt/SDK reservation and ambiguity handling, still requiring
  a *separate* review and activation step;
- hard bounds for claim, ACK, and the report; no arbitrary tasks.

**This phase is NOT the actual SDK test.** It intentionally makes **zero DB
writes**, makes **no SDK calls**, does **not** edit `opencode.json`, and does
**not** touch or restart any services. The next phase must coordinate
exclusive canary dispatcher ownership and lifecycle of the actual MultiTool
process BEFORE sending the test message. Never operate both old and new
plugins simultaneously against the canary.

Preflight conditions are frozen to the state after Stage 33E: canary last_seq 4,
ChatGPT turn, no Stage 33F messages yet. If those changed, STOP; do not force
resume. The SQL read-only check uses only allowed SECURITY DEFINER functions.
The new agent CLI has write functions but only inside the *unregistered*
candidate; `38_prepare_sdk_candidate.sh` never invokes those operations.

Caveat: a separate `bridge_agent` login is NOT equivalent to isolated OS
credentials while both secret files are accessible to Linux user `kali`.
Production separation will require dedicated UNIX identities or a secret
manager. The loopback-only 18772 MCP without OAuth must NOT be tunneled.

## Execute once, as kali

```bash
sha256sum -c SHA256SUMS
python3 -m py_compile stage33f_agent_cli.py render_candidate33f.py inventory33f.py test_stage33f.py
python3 -m unittest -v test_stage33f
for f in *.sh; do bash -n "$f"; done
bash 38_prepare_sdk_candidate.sh
bash 38_verify_only.sh
```

Before actual activation the operator must send full JSON inventory plus the
candidate source hash to ChatGPT. **Never run the installer a second time.**

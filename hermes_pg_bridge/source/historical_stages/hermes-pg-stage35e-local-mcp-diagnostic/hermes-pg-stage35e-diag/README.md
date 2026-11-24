# Hermes PostgreSQL bridge — Stage 35E isolated local MCP diagnosis

ChatGPT-authored on 2026-11-23. This package is **not** a tunnel switch, unit update,
OAuth registration, DB migration or rewrite of Stage 35C/35D.

## Diagnosis

The Stage 35D `STAGE35D_LOCAL_MCP_MISSING_AND_WRONG_KEY_DENIED` marker is emitted
only *after* `/ping` returned HTTP 200 and both unauthenticated and wrong-key MCP
requests returned 403. Therefore the 35C Uvicorn ASGI process was responsive.
Stage 35D `smoke35d.py:fetch` calls `response.read(131072)`. For Streamable HTTP
SSE it may block awaiting the end/size of a response stream that stays open.
This is a strong hypothesis (not yet a confirmed live root cause); Stage 35E
probes `initialize`, `tools/list` and two read-only tool calls with bounded
`http.client.HTTPResponse.read1()` and returns at the first complete JSON-RPC
response event, without waiting for EOF.

## Safety

- Starts a completely separate *one-time* diagnostic Python process bound ONLY to
  `127.0.0.1:18777` with `uvicorn.run(... log_level='info', access_log=False,
  timeout_graceful_shutdown=5, lifespan='on')`.
- Doesn't use `systemctl start/stop`, change the original `35C` unit, `121`,
  7-plugin `opencode.json`, tunnel on 18773, PostgreSQL, Git or any MCP connector.
- Reads the private header secret at runtime; never displays it.
- Doesn't call *any* MCP write tools. `managed_health` and `managed_list` use the
  existing role-bound read-only DB functions only. Server initialization creates
  an in-memory MCP session, not a managed dialogue.
- Requires the Stage 35D pinfixed source, its one-shot attempt proof, pinned
  previous 35C source files, unchanged seven-plugin configuration, active existing
  tunnel, and **absence** of the managed write permit.
- A new 35E attempt marker is created with O_EXCL and mode 0600 *before* the
  diagnostic binds a port. The procedure must not be retried on a failure.
- Waits up to **60 seconds** for a responsive `/ping`. Prints stage markers
  for every MCP step. On completion or exception sends SIGTERM to the exact child
  process group; kills only that child group after 10s if needed. A forced SIGKILL is reported
  as a failure, even if MCP calls worked.
- Full `info` startup/shutdown log and Python traceback are captured in a
  private `stage35e-uvicorn-info.log` (mode 0600). `log_audit35e.py` prints only
  allowlisted, non-secret event names and exception classes. **Do not paste the
  raw log without separately checking it for credentials**.

## Running

Place the ZIP and accompanying installer in `/home/kali/Downloads` as user `kali`.
Run the pinned installer. It performs archive integrity, offline test suite,
`130_readonly_preflight.sh`, then **one** `131_run_diag_once.sh` attempt.
`132_postcheck.sh` can safely be called manually afterward; it is read-only.

Send ChatGPT the full `STAGE35E_*` markers (including failure markers),
`STAGE35D_*` preflight markers, and the sanitized log summary. Do not run `121`.

# Stage 35F: local MCP shadow receive() probe (one-shot)

Scope: **only** a new, isolated process at `127.0.0.1:18777`, once. No changes to Stage35C/D/E, user systemd, original Secure MCP tunnel at `127.0.0.1:18773`, seven OpenCode plugins, remote DB, dispatch, or write permit.

## Root-cause hypothesis

The original `MCPStaticGate`'s inner `once()` function unconditionally generated `{'type':'http.request','body':b'','more_body':False}` on every receive call after the first replay. This fabricates an *infinite stream of request events* rather than forwarding the underlying receive channel. MCP Streamable HTTP may loop while listening for real disconnects, preventing HTTP response and exhausting the ASGI event loop. This explains both the initialize timeout and expensive shutdown as a hypothesis, not yet a confirmed production fix.

`guard35f.py` is a byte-for-byte copy of the pinned 35C `static_guard35c.py` except **one line**: after replay of POST body, later calls now `await receive()` instead of generating synthetic `http.request` messages. This exact one-line change is runtime-checked against installed 35C before any socket opens. The original source SHA256 must equal `643216a27641b915651cb4c9685d81789af5b7dbf4a19f17741d6bfdae40d06c`. The fork SHA256 must equal `695622407566b316482f676da0bf3c5a63a12b3f4b6e6d706c92130a760aaa88`.

## Network semantics

- Only localhost endpoint `/mcp`; headers include **both** JSON and SSE media types and a private key read from existing private file (never printed).
- Streamable HTTP SSE is decoded incrementally until a complete matching `data:` JSON-RPC event, not EOF. Chunked HTTP works.
- Output includes only response status, content-type category and allowlisted safe **prefix hex**, never arbitrary bytes/response bodies, credentials or tokens.
- `initialize`, `notifications/initialized`, `tools/list`, `managed_health`, `managed_list` only; never call the four managed write tools. A successful session gets a local MCP DELETE to clean up in-memory state; not a database write.
- Clean termination preferred. If SIGTERM takes more than 10 seconds, private diagnostic stack is requested via SIGUSR1, then only this subprocess group is SIGKILLed. Never kill systemd user service or the original tunnel. Child forced SIGKILL is treated as failure.

## Install and run

SHA-check the ZIP against the digest provided by ChatGPT **before extraction**. Under `kali`, extract once into a new, disposable private directory and run `140_install_and_diag_once.sh` from inside. It verifies manifest, source parser, unit tests and pinned preflight, installs to `/home/kali/hermes-pg-stage35f-guard-probe`, then runs **one** diagnostic. On failure, keep the mark and send back only emitted `STAGE35F_*` markers. Do not retry or edit agent-side code.

Raw server info and Python stack traces (if requested) remain in `stage35f-uvicorn-private.log` mode 0600; do **not** upload/print full log as it may include implementation details. `log_audit35f.py` emits only allowlisted indicators.

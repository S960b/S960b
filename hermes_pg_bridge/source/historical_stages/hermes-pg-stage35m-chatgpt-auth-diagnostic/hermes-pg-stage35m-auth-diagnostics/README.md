# Stage 35M — ChatGPT connector 403 diagnostic

Read-only diagnostic for live Stage 35L cutover. No service/unit/DB changes, no keys printed. The private key files are inspected with `lstat()` only; their contents are never read. Reads localhost 18778 (`/readyz`, `/api/status`, `/metrics`), reads user systemd service status and filtered two-hour journal event counts, tests unauthenticated MCP rejection, and checks launcher flags without showing their values. Does not probe Platform APIs or require additional credentials.

Run with `python3 diag35m.py` as kali; script is safe to rerun if the user repeats a ChatGPT connection attempt.

**Expected**: `/readyz=200`, `/mcp` without header `403`, `MCP_OAUTH_DISCOVERY=disabled`, `file_header=true`.

If control-plane polls are not successful, investigate tunnel permission/runtime key/associated ChatGPT workspace. If polls are healthy and no MCP JSON-RPC requests reach the local service during user connector creation, inspect the ChatGPT app auth mode (No authentication), tunnel selector, and workspace permission. If MCP requests arrive and return 403, inspect header forwarding in real production tunnel; do not remove backend gate.

Do not paste unsanitized systemd logs, full status JSON, environment or secret file bytes.

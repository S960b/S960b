# Stage 35R - short-lived connector/auth diagnostic

ChatGPT-authored. The operational route changes **temporarily** for diagnostic capture, but the MCP runtime, the secrets, the database and installed ChatGPT plugin configurations do **not** change. The original tunnel ID and client binary are retained. The only difference in the temporary tunnel launcher is the target origin port, `18777` -> `18779`. The new TCP observer listens only on `127.0.0.1:18779` and passes bytes to the original MCP `127.0.0.1:18777`.

## Procedure

1. Copy this archive into `/home/kali/Downloads`. From an extracted, checksum-verified package run `230_install_and_activate_once.sh` exactly once as user `kali` (with no sudo). After marker `STAGE35R_NOW_ATTEMPT_CHATGPT_PLUGIN_CREATION_ONCE_THEN_COLLECT`, attempt to create a plugin for the existing tunnel **once**, choosing **No authentication**.
2. Have the agent run `bash /home/kali/hermes-pg-stage35r-connector-observer/231_collect_restore.sh` immediately after that attempt. This restores the original 18777 route and returns *only safe aggregate classifications*.
3. If the operator does not finish within 12 minutes, a **user systemd timer** automatically attempts restoration. On any unexpected state, stop further experiments and return the marker; owner can run `232_emergency_restore.sh` to attempt immediate restoration.

The observer records only first HTTP request per TCP connection (not every keepalive request), method and sanitized route, authenticated-header presence / constant-time equality, RPC method from a short bounded JSON request, and HTTP status. It does not store HTTP bodies, header values, request IDs, URLs, secrets or raw traffic. If there are zero observed requests but the tunnel emits an error, the failure likely precedes the loopback MCP hop. If observed traffic reaches MCP and returns HTTP 403 with missing/wrong local header, the local injection is implicated. Matching headers with 403 require an independent check of the MCP authorization/method logic.

Do **not** assume `harpoon unsupported_channel` proves a stale ChatGPT plugin: that event can be unrelated to the main MCP channel.

## Recovery notes

A single temporary systemd drop-in is placed at `~/.config/systemd/user/hermes-pg-tunnel-stage35jk.service.d/90-stage35r-observer.conf`. The original launcher is verified by SHA-256 and not edited. The drop-in is removed on completion. Restore keeps the observer and auto-restore service unit definitions installed but inactive for forensic traceability. The pre-existing tunnel identity and old tunnel service remain unchanged. If restore reports a critical error, do not retry blindly or create a second tunnel process; inspect the exact unit status first.

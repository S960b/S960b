# Stage 35S — MCP observer with mandatory upstream HTTP preflight

**Authored by ChatGPT.** Separate one-shot retry after Stage35R failed readiness.

## Changes compared to 35R

* Verifies Stage35R completed rollback before any modification. Prints only safe aggregate counts from previous `events.ndjson` (which has fixed-schema metadata and never body/headers).
* Launches a NEW local observer on `127.0.0.1:18779` **before** changing the tunnel launcher. Requires `/ping=200`, then uses a streaming MCP client to confirm `initialize` and **exactly 11 expected tools** through this observer. No write-tools are invoked. This is stronger than a TCP listen test.
* Only after preflight passes, installs a temporary systemd drop-in for **the existing tunnel identity** and arms a 12-minute automatic restore timer before restarting the tunnel. Requires systemd tunnel active + observer active + MCP active + `/readyz=200` and a changed MainPID. Wait up to 115 seconds. If this is never true, reports safe state and observed HTTP/auth counters, restores the exact original route and stops (do **not** attempt ChatGPT plugin creation).
* If the preflight fails **before** changing the tunnel, restores the observer and units without restarting the otherwise healthy tunnel.
* No modification of original MCP code, original launcher, secret, ChatGPT plugin config, DB data, managed write permissions, or legacy processes. Captures only first HTTP request per TCP connection; body is never persisted. Header equality checked in memory with constant-time comparison. Logs no private text, credentials, bodies, or URLs. Uses source/hash and exact drop-in checks, no blanket chmod.

## Operator sequence

1. Agent: run `240_install_and_activate_once.sh` exactly **once** as `kali` (no sudo) from the extracted package after verifying the archive SHA-256 and SHA256SUMS. The package installs in `/home/kali/hermes-pg-stage35s-connector-observer` and begins its temporary 12-minute diagnostic window.
2. Only if `STAGE35S_NOW_ATTEMPT_CHATGPT_PLUGIN_CREATION_ONCE_THEN_COLLECT` is printed, user performs **one** ChatGPT plugin creation attempt using the existing tunnel and **No authentication**. Do not attempt otherwise.
3. Agent: immediately run `bash /home/kali/hermes-pg-stage35s-connector-observer/241_collect_restore.sh`, return only `STAGE35S_*` markers, no raw journal. The tunnel target is restored to `127.0.0.1:18777/mcp` and observer stopped. If the user does not return, systemd timer automatically attempts to restore after 12 minutes.
4. The independent `242_emergency_restore.sh` can restore only after an explicit instruction. **Never rerun `240`**, even after preflight failure.

## Limits

* No remote ChatGPT connectivity can be proven until a real app creation attempt.
* If the tunnel client's health check remains unhealthy even though direct HTTP preflight passed, activation deliberately fails and rolls back; diagnostics distinguish the two conditions.
* HTTP `DELETE` may return 501 from the MCP; the preflight records it as non-fatal. The probe makes a fresh temporary MCP session but does not mutate database rows.
* Do not treat an unsupported `harpoon` channel as proof of stale plugin state.

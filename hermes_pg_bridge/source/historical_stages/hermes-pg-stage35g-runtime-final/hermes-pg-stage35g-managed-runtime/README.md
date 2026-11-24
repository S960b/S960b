# Stage35G: Permanent isolated ASGI gate repair for inactive 35C shadow

Authored by ChatGPT. Agent executes only the exact script and returns markers; no edits.

## Scope

- Source `static_guard35c.py` stays **byte-identical** to the original Stage35C SHA pin.
- `guard35g.py` is the **exact same** single-line ASGI disconnect fix proven by Stage35F (SHA `6956224...`).
- `service35g.py` loads the pinned 35C server, swaps only the guard wrapper, runs uvicorn with `lifespan='on'`, loopback port 18777, info logs, graceful-shutdown timeout 5 seconds.
- The override `/home/kali/.config/systemd/user/hermes-pg-stage35c-static-shadow.service.d/35g-patched-runtime.conf` replaces only `ExecStart`, sets `Restart=no`, leaves base service and every other unit unchanged. It is **not enabled**.
- One-shot smoke starts only 35C, verifies missing/wrong key (403), `initialize`, `tools/list` 11 known tools, `managed_health`, `managed_list` (read-only role-bound PG); cleans up session and stops unit.
- Existing active tunnel remains on 18773; port 18777 closed at end. No DB writes or write permit; no plugin changes; no old one-shot reruns.
- CLI capability check reads **only** `tunnel-client run --help` with sanitized environment, never starts a tunnel. **It does not prove `file:` secret expansion**; that requires a later controlled test, not an assumption.

## Safety and partial failures

The stage must run once with `kali` in a fresh install directory. File and unit edits are exclusive and fail closed. The one-shot smoke marker is written before systemd start. If it fails, **do not retry** and do not execute Stage35D/35E/35F again. Stop and provide markers. If it fails after override installation, its override remains installed for inspection, and only the local 35C service is stopped; no tunnel cutover occurs. If SIGTERM does not stop within 20 seconds, the script records this and targets only the 35C user service with SIGKILL. This is a failed smoke, not success.

## Expected success markers

- `STAGE35G_PACKAGE_MANIFEST_AND_21_TESTS_OK`
- `STAGE35G_SYSTEMD_EFFECTIVE_EXECSTART_PATCHED_RESTART_NO_INACTIVE`
- `STAGE35G_SERVICE_AUTH_DENIED_403_TWICE`
- `STAGE35G_SERVICE_INITIALIZE_OK_FRAMING_SSE` (JSON possible)
- `STAGE35G_SERVICE_TOOLS_LIST_11_EXPECTED_NO_WRITE_CALLS`
- `STAGE35G_SERVICE_ROLE_BOUND_PG_READS_PILOT_DISABLED`
- `STAGE35G_SERVICE_STOP_METHOD_sigterm`
- `STAGE35G_POSTCHECK_18777_CLOSED_18773_ACTIVE_PERMIT_ABSENT`
- `STAGE35G_CLIENT_EXTRA_HEADERS_FLAG_PRESENT_FILE_REFERENCE_NOT_PROVEN`
- `STAGE35G_COMPLETE_PATCHED_SYSTEMD_SHADOW_VERIFIED_STOPPED_OLD_TUNNEL_UNCHANGED`

Do not paste raw journal output, session IDs, secrets, SQL results or environment values. Return only Stage35G markers, presence/absence state and scrubbed CLI help flag line.

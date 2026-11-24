# Hermes PG v2 — Stage 33F-D: Secure MCP Tunnel role-bound canary cutover

**Purpose:** Switch the **existing** OpenAI Secure MCP Tunnel `hermes-pg-v2` from the Stage 31 `bridge_app` one-shot MCP at `127.0.0.1:18769/mcp` to the **already running** role-bound Stage 33F-B test MCP at `127.0.0.1:18773/mcp` (`bridge_chatgpt` only). Leave the already-loaded Stage 33F agent plugin (`bridge_agent`) untouched. Do **not** send `seq=5` during installation.

The user has confirmed Stage 33F-C committed: Git plugin removed, previous PG canary plugin removed, seven plugins total, one exclusive dispatch lock, current canary `seq=4`, no test send. Installer rechecks exact config and plugin SHA, current roles, canary state, existing tunnel and local MCP, Git services disabled, and the absence of the arm file.

## Order and responsibilities

1. **ChatGPT** wrote and tests this package; **user** downloads and passes instructions; **agent** runs the commands without code changes.
2. Agent verifies archive checksum, offline tests and executes **`41_install_and_switch.sh`** once. This starts a new tunnel-client service on port **18774**, stops the previous tunnel runtime on 18770, checks `/readyz`, runs independent read-only receipt check, and rolls back if the switch fails. Does **not** change MultiTool, PostgreSQL, or SDK; does **not** create the arm file.
3. **User** adds a **new** ChatGPT Custom MCP Plugin on the existing tunnel `hermes-pg-v2`, with no additional authentication for this narrowly bounded temporary test. Name e.g. `Hermes PG Stage33F Role Test`. The old `Hermes PG Write Canary` remains only as historical test. **Do not connect the Stage 33F-B service to a public URL**.
4. **ChatGPT** calls `stage33f_actor_health` and `stage33f_canary_status` via the **new plugin** and verifies `actor_role=bridge_chatgpt`, `seq=4`, and `test_task_committed=false`.
5. ONLY after that, ChatGPT requests agent run `STAGE33FD_CHATGPT_PLUGIN_VERIFIED=YES_ROLE_BOUND_18773 bash 42_arm_after_plugin_check.sh`. The arm file is precisely 0600 and contains a fixed magic line. Does **not** itself send messages.
6. **ChatGPT** calls `stage33f_send_once` exactly once in the ordinary chat. It sends only the fixed canary `seq=5` under `bridge_chatgpt`. MultiTool's `bridge_agent` plugin handles the task through SDK and publishes exact report `PGV2_ROLE_BOUND_MT_SDK_ACK_20261122` on `seq=6`.
7. ChatGPT calls `stage33f_canary_status`: success requires `seq=6`, `test_task_committed=true`, `test_report_exact=true`, `task_delivery_status=delivered`, and `sdk_roundtrip_complete=true`. Agent may run `44_readonly_status.sh` for diagnosis, never rerun installation or resend an unconfirmed SDK prompt.

**This is a test-only unauthenticated MCP:** exposed through the user's private OpenAI tunnel and LIMITED to one immutable harmless message. It is not general-purpose production authorization. Do not expand its parameters or role powers. Do not arm before step 4.

### Agent install commands (run from `kali`)

```bash
cd /home/kali/hermes-pg-stage33fd-tunnel-cutover
sha256sum -c SHA256SUMS
env -u APPDIR -u APPIMAGE python3 -m unittest -v test_stage33fd
for f in *.sh; do bash -n "$f"; done
env -u APPDIR -u APPIMAGE python3 -m py_compile verify33fd.py test_stage33fd.py
bash 41_install_and_switch.sh
bash 41_verify_only.sh
```

### Rollback

ONLY before the arm file exists and before any seq5 send: `bash 43_rollback_before_arm.sh` from this directory. It restores the old Stage 31 tunnel runtime. Never use this after arming or after `seq=5`; reconcile durable database+SDK state first.

### Important limits

- `hermes-pg-v2-stage33fb-cg.service` MCP port 18773 is local, no OAuth. The tunnel is the only connector. All current plugins and other services stay unchanged.
- The test does not **prove** that multiple app processes can never cause duplicates in a crash; the plugin's durable reservations and lock must still be independently tested before production.
- Stage 33F-D only validates a fixed canary, not general arbitrary task traffic or production authentication; production MCP must use proper identity, secret isolation and appropriate authorization.

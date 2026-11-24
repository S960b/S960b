# Stage 33C — Durable SSH shadow forward and actor-bound API adapters

**Goal:** address the intermittent unmanaged SSH forward while preparing safely separated PostgreSQL clients. Existing 15432 is left untouched. Stage 33C provisions a *second* independently supervised SSH local forward on 127.0.0.1:15433, strict host-key pinning, separate user systemd service `bridge-pg-v2-ssh-shadow.service`, automatic reconnection and safe read-only integration checks with both separate Stage 33B PostgreSQL logins.

The supplied `actor_adapter.py` is a **draft adapter only**. It is NOT automatically loaded into running MCP or MultiTool; those still use legacy `bridge_app` until we test a separate instance and authorization. `ChatGPTActorDB` and `AgentActorDB` call only the role-specific Stage 33A SQL wrappers; callers cannot pass a sender or actor role. Actual authorization remains enforced by the database. The password files stay private to Kali and are read in process only, never printed or passed on a shell command line.

### Important boundaries

* New service uses a **shadow port** 15433 to avoid killing the old unmanaged tunnel on 15432. Until a future client cutover, the legacy tunnel may still disappear; new 15433 will have automatic reconnection. Avoid disabling the old tunnel or claiming full failover.
* The new SSH endpoint is supplied **privately** via `PG_V2_SSH_TARGET`, with host key verified by existing `~/.ssh/known_hosts`; no old hostname is embedded.
* Existing SSH private key `/home/kali/.ssh/mt_tun_ed25519` is used through `IdentitiesOnly=yes`. No password stored in systemd unit. Endpoint saved in a private file mode 600; user config/actor directory permissions are checked.
* `systemctl --user enable --now` gives reactivation when the user session starts. To run after a fresh VM boot before login, Linux user lingering must be configured separately, **not** by this script.
* This stage does **not** open remote PostgreSQL ports, modify SQL roles/functions, change any existing service/Taskboard/plugin/tunnel, send a message or acknowledge an outbox delivery.
* This does **not** achieve Unix process isolation: both role secrets remain readable by the same `kali` OS user. Proper isolation is required before production deployment.

### Agent procedure

On Kali (user `kali`), obtain the SHA256-verified package and unzip into `/home/kali/hermes-pg-stage33c-shadow-tunnel` (must not already exist). Set `PG_V2_SSH_TARGET` privately to the previously authorized current root SSH endpoint (without echoing it). Execute unit tests, then `bash 35_install_shadow_tunnel.sh`. If the script fails after installation begins it automatically disables and removes only the new shadow unit and its private target config. On success run `bash 35_verify_shadow_tunnel.sh` and return only safe marker lines.

`bash 35_disable_shadow_tunnel.sh` is a separate rollback, to be used only when ChatGPT explicitly instructs it. It does not touch port 15432.

Expected markers:

```
STAGE33C_SHADOW_PRIVATE_TARGET_CONFIGURED
STAGE33C_SHADOW_DB_REAL_LOGINS_OK
STAGE33C_ROLE_BOUND_READ_ONLY_API_OK
STAGE33C_CANARY_UNMODIFIED_LAST_SEQ_4
STAGE33C_READ_ONLY_SHADOW_FORWARD_VERIFIED
STAGE33C_PERSISTENT_SHADOW_SSH_UNIT_ACTIVE
STAGE33C_LEGACY_15432_AND_ALL_OTHER_SERVICES_UNCHANGED
```

Existing `APPDIR` and `APPIMAGE` AppImage exports are removed when running psycopg Python, matching Stage 33B operating knowledge.

# Hermes PG Bridge v2 — Stage 33B: Separate authenticated PostgreSQL roles

## Scope

This is **credential provisioning and a negative security test only**, not a client cutover. It changes exactly two existing `NOLOGIN` PostgreSQL roles (`bridge_chatgpt`, `bridge_agent`) to `LOGIN` with different high-entropy passwords. No message, outbox, task, plugin, tunnel, systemd unit, or legacy `bridge_app` permission is modified.

Unlike `SET ROLE` tests, verification makes two genuinely authenticated PostgreSQL connections through the existing Kali SSH forward at `127.0.0.1:15432`, checks `SESSION_USER`, calls `health`, and verifies that the opposite actor's wrapper and `SET ROLE` both fail with SQLSTATE `42501`.

The passwords are generated locally on Kali and stored as private libpq `.pgpass` files, one per role in `/home/kali/.config/bridge-pg-v2/actor-credentials/` (directory `0700`, files `0600`). Only **salted SCRAM verifiers**, not plaintext passwords, are transmitted to the server through the already-authorized SSH channel. Neither plaintext password, verifier, hostname, nor DSN is printed.

`PG_V2_SSH_TARGET` must be set **privately** using the currently working, host-key-pinned root SSH connection to the DB server. **Do not use the obsolete hostname** previously mentioned; do not embed IPs into scripts. Kali uid is expected to be 1000. If the SSH tunnel at `127.0.0.1:15432` is offline or database authentication rejects the new role, the test stops and keeps the role secrets for investigation. No automatic re-provisioning or credential overwrite occurs.

## Stages

1. `sha256sum -c SHA256SUMS`, `python3 -m unittest -v test_stage33b`, `bash -n`.
2. `bash 34_provision.sh` exactly **once**. It checks SQL preconditions under the root SSH authority, creates secret files `O_EXCL` + `O_NOFOLLOW`, applies both LOGIN changes in one database transaction, authenticates both logins over the SSH tunnel, and validates permissions with read-only SQL. It never writes to dialogs/outbox.
3. `bash 34_verify_only.sh` runs read-only authenticated checks without creating accounts or credentials. Use if checking the installed state later. Do not repeat provision.

Expected success markers:
- `STAGE33B_SHADOW_PREFLIGHT_OK`
- `STAGE33B_SEPARATE_SCRAM_LOGINS_CREATED`
- `STAGE33B_REAL_LOGIN_CHATGPT_AND_AGENT_OK`
- `STAGE33B_CROSS_ACTOR_FUNCTION_AND_SET_ROLE_DENIED`
- `STAGE33B_NO_DIRECT_TABLE_ACCESS`
- `STAGE33B_READ_ONLY_VERIFICATION_COMPLETE`
- `STAGE33B_CREDENTIAL_FILES_600_PRIVATE`
- `STAGE33B_LEGACY_CLIENTS_NOT_SWITCHED`
- `STAGE33B_READY_FOR_CLIENT_ADAPTER_DESIGN`

## Security boundary and what is *not* yet proved

This proves SQL ACL and actor identity under actual login sessions. It **does not** yet prove a safe deployment of the ChatGPT or worker adapters, local filesystem isolation between processes owned by Kali, per-dialog reader isolation, credential rotation, or durable ChatGPT inbox acknowledgement. Subsequent stages must run client adapters with distinct protected credentials and only then revoke shared `bridge_app` access. Running both services under the same UNIX user means file permissions alone do not isolate the two credentials from a compromised process: production should use distinct UNIX service users or a secrets manager. No existing service is changed here.

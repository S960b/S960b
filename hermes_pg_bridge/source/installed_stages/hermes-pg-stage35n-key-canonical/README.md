# Stage35N — atomic local MCP header key canonicalization

Purpose: eliminate the known 65-byte local secret-file ambiguity (64 chars + LF), while keeping the actual key unchanged. This does **not prove** that a trailing newline caused the ChatGPT-side 403. Stage35H successfully exercised a real dev-proxy client against the same file reference; a ChatGPT workspace tunnel ACL or connector registration problem is also possible.

- One-shot under `kali`. Fixed marker: `stage35n-attempt.json`. No automatic retries.
- Preconditions: Stage35L active; managed MCP and new tunnel both active; old tunnel inactive; pinned launcher, seven-plugin config, no DB-write permit. No OAuth or App modifications.
- Require a **single LF** after exactly 64 URL-safe characters; reject any other file shape.
- Atomic replacement of the **same secret file** with exactly 64 original bytes (mode 0600). No secret rotation, no output of secret or hash.
- Restart **only** `hermes-pg-tunnel-stage35jk.service`; allow at most one restart, verify port 18778 /readyz and 18777 /ping plus systemd state. If failure, restore exact original bytes with LF and restart once again; if restore restart also fails, report CRITICAL and stop. Do not automatically start an unverified old tunnel.
- Never modify 35C code, PG state, `opencode.json`, tunnel ID, or tunnel launcher. Does not enable systemd unit at boot.
- On success, the actual external ChatGPT connector creation with **Tunnel** and **No authentication** must be tested again. This stage alone cannot prove connector RBAC/workspace association.

To run, follow the single user-provided command (installer `200_install_and_apply_once.sh` from verified zip). The `201_verify_only.sh` is non-mutating.

**No raw logs/secrets** in agent report. Report STAGE35N_* markers and failure codes only.

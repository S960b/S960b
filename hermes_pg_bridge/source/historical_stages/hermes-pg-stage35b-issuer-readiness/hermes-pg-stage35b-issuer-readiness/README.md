# Stage 35B — OAuth issuer selection and Keycloak readiness (READ ONLY)

## Decision

**Production plan: Keycloak as an independent OAuth 2.0/OIDC authorization server** on the VPS, with its own **public HTTPS FQDN**. The **MCP service stays private** behind OpenAI Secure MCP Tunnel. Do **not** reuse legacy `require_owner()` or SQLite OAuth tokens as if they were RS256 issuer-signed access tokens. A static audit of the old 8766 code is included for evidence, but no credentials are extracted.

Keycloak currently documents CIMD and OAuth Resource Indicators as **experimental** features. This is a reviewed version- and config-pinned dependency, not a guarantee of instant interoperability. Use a staged pilot with an external HTTPS realm; don't put Keycloak in front of the production tunnel until a real ChatGPT OAuth/PKCE roundtrip succeeds.

Stage35A's resource server currently accepts **only RS256 JWT access tokens with an explicitly pinned issuer, audience, exact `sub`, and scopes**. A Keycloak realm is expected to issue such tokens *after separately configuring a resource/audience mapping, scopes, user and client policy*. Merely running Keycloak does not satisfy this contract.

## What this package does

All normal procedures are **read-only** (apart from extraction of package files by the agent). There is **no SQL migration, no systemd installation or start, no OAuth configuration, no token minting, no write gate, no service or firewall changes, and no tunnel cutover**.

- `readiness35b.py` verifies the pre-pilot 7-plugin config, both PostgreSQL actor logins and receipts, inactive Stage35A server, no OAuth/write permits, and the current operational 18773 tunnel.
- `113_legacy_oauth_static_audit.sh` parses *known local source files* without executing them; reports only code-feature booleans, counts and source SHA256. In particular, it distinguishes a local owner-login/OAuth provider from an **RS256 JWT resource issuer**. It does not print source lines, environment variables, secrets or message bodies. Its result is **not** a security certification.
- `114_vps_readiness_readonly.sh` uses the existing pinned SSH identity and target, does not elevate privileges, and reports only broad binary readiness booleans (container engine, reverse proxy, RAM/disk thresholds, presence of TCP/443 listener). No VPS address or service names are printed.
- `115_auth_gate_offline.sh` checks that Stage35A still fails closed and re-runs the original 50 Stage35A authentication tests with test fixtures. Does not start MCP.
- `metadata35b.py` checks a **public Keycloak OIDC issuer** only when later invoked explicitly with `--issuer https://your-real-domain/realms/<realm>`; by default it makes **zero HTTP requests**. Uses read-only HTTPS discovery, no redirects, bounded results. It reports capabilities only, never access tokens. `scopes_supported` and CIMD may be absent until the realm is configured: those are reported as false, not silently assumed.

## Installation & execution

The package is intended for user `kali` at `/home/kali/hermes-pg-stage35b-issuer-readiness`. Verify the SHA of the ZIP before unpacking; verify all `SHA256SUMS`. Use the already-installed `/home/kali/bridge-pg-v2/.venv/bin/python` and unset `APPDIR`/`APPIMAGE`.

1. `python -m unittest -v test_stage35b` (30 offline tests)
2. `python -m py_compile readiness35b.py metadata35b.py test_stage35b.py`
3. `bash -n 11*.sh`
4. `bash 112_readonly_preflight.sh`
5. `bash 113_legacy_oauth_static_audit.sh`
6. `bash 114_vps_readiness_readonly.sh`
7. `bash 115_auth_gate_offline.sh`
8. `bash 116_verify_readonly.sh`

On failure, **stop**; these checks may be repeated because all are read-only. No SQL writes are permitted.

## Minimum prerequisites for next stage, Stage35C

1. **Choose/control one stable, publicly reachable HTTPS FQDN for the Keycloak issuer.** Avoid old unverified `is-a.dev` names and ephemeral quick-tunnel hostnames. TLS needs a trusted certificate. HTTP `:8080`/Keycloak admin must not be exposed to the public without separate hardening.
2. **Capacity** for a separate Keycloak instance + own backing DB. Probe uses **2 GiB available RAM / 8 GiB free disk** as preliminary human-review thresholds, not vendor minimums. Prefer pinned container image digest, dedicated system user, isolated DB/user, backups and a host firewall. Do not share PostgreSQL `bridge_chatgpt`/`bridge_agent` credentials with Keycloak.
3. **Keycloak realm**, MFA-enforced owner login, access-token TTL <= 1 hour, and explicit *client scope* `managed:read` (later `managed:write`); configure the audience to the exact MCP resource URL (not an arbitrary Keycloak client ID). Validate an actual access token signature, audience, scope and subject before allowing any reads.
4. **ChatGPT OAuth authorization-code + PKCE S256**, client identification via **CIMD** when supported and configured (or restricted DCR/pre-registered client as a reviewed fallback). Restrict CIMD client policy to the exact expected ChatGPT metadata document/redirects and approved MCP resource; no open wildcard DCR. Config must be reviewed and tested with OpenAI's actual callback.
5. A **second, isolated test tunnel** so the existing stage33fd tunnel to 18773 continues unaffected. Stage35A MCP at 18776 stays inactive until the issuer is validated; initial tests must allow only `managed:read` and no write permit.
6. Plan for public JWKS rotation. Stage35A's JWKS is currently a local pinned file, **not auto-refreshed**; monitor rotation and stage manual key refresh before it expires. Never import private issuer signing keys into Kali.

## Strict non-goals

Do not start Keycloak, bind external listeners, open ports, create OAuth config, enable dispatch, enable the managed agent plugin, or switch the existing tunnel as part of Stage35B.

## Authoritative docs (consult again when configuring)

- https://www.keycloak.org/securing-apps/mcp-authz-server
- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://www.rfc-editor.org/rfc/rfc9728.html

## Expected markers

- `STAGE35B_7_PLUGIN_CANARY_AND_QUIESCED_PILOT_PINNED`
- `STAGE35B_TWO_ROLE_DATABASE_AND_RECEIPTS_READONLY_OK`
- `STAGE35B_OAUTH_SHADOW_INACTIVE_NO_CONFIG_NO_WRITES`
- `STAGE35B_18773_TUNNEL_AND_CANARY_RUNNING_UNCHANGED`
- `STAGE35B_REQUIRE_OWNER_IS_NOT_PROOF_OF_OAUTH_ISSUER_OR_JWT`
- `STAGE35B_LEGACY_STATIC_AUDIT_ONLY_NO_CODE_OR_SECRET_DUMP`
- `STAGE35B_VPS_REMOTE_READONLY_ACCESS_OK`
- `STAGE35B_VPS_READINESS_PROBED_NO_CHANGES`
- `STAGE35B_SHADOW_OAUTH_FAIL_CLOSED_CONFIG_ABSENT`
- `STAGE35B_MCP_READ_WRITE_SCOPES_DISTINCT`
- `STAGE35B_STAGE35A_OFFLINE_AUTH_TESTS_PASSED`
- `STAGE35B_FINAL_VERIFY_NO_SERVICE_OR_DATABASE_MUTATIONS`

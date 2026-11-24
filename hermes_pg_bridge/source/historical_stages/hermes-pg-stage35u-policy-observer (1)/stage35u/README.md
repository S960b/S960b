# Stage 35U — Gate policy diagnosis, no auth expansion

The 35T observer captured two MCP 403s *with the correct key*, but classified their RPC methods as `other`. The cause is not yet established. In `guard35g.py`, POST RPC methods outside a small static allowlist are rejected 403, and OPTIONS/HEAD on /mcp are also rejected.

This package does NOT alter MCP code, auth, PostgreSQL, or plugins. It first summarizes the old 35T 403 events without raw fields. Then it runs an isolated new observer on 127.0.0.1:18779 with a short, reversible tunnel redirect, pending one new connector-creation attempt. The observer reports HTTP method, *curated* RPC method name (other strings map to `other`), predicted gate branch, and upstream status. No secrets, bodies, arbitrary methods, IDs, URLs, or raw headers are logged. The gate prediction is based on a hash-pinned guard35g source and may differ from the final server response.

## Procedure

1. Verify outer archive SHA, extract into a fresh private directory; run `260_install_and_activate_once.sh` as `kali`. Requires 35T fully restored.
2. ONLY when `STAGE35U_NOW_ATTEMPT_CHATGPT_PLUGIN_CREATION_ONCE_THEN_COLLECT` appears, attempt a new ChatGPT connector with the existing tunnel and No authentication **once**. Report the outcome immediately.
3. Run `261_collect_restore.sh` from the installed directory. It restores the original 18777 route and prints sanitized per-403 denial labels.
4. After 12 minutes the systemd timer restores automatically if collection did not occur. `262_emergency_restore.sh` is for explicit break-glass only. Never rerun activation.

## Interpret

For example, `DENIAL_403_01_HTTP_POST_RPC_RESOURCES_TEMPLATES_LIST_PREDICTION_DENY_RPC_METHOD_AUTH_MATCH` means the local strict allowlist would reject a standard discovery RPC. Do NOT interpret GET/DELETE `RPC_NON_RPC` as invalid JSON; those requests have no body by design. If classification is `body_unobserved` or `other`, do not claim the precise method is known. No policy patch is applied by this package.

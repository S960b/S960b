PG Bridge v2 — temporary HTTPS test only

What this stage does:
- Requires healthy MCP on localhost:8766 and SSH tunnel to PostgreSQL.
- Installs cloudflared into kali's ~/.local/bin only if its GitHub release provides a digest that can be verified.
- Creates a NEW temporary Cloudflare Quick Tunnel systemd user unit. The unit is NOT enabled at boot.
- Sets MCP public OAuth base URL using an isolated systemd drop-in and restarts ONLY bridge-pg-v2-mcp.service.
- Verifies external HTTPS ping, unauthenticated MCP 401, WWW-Authenticate metadata path, OAuth issuer/authorization/token/registration endpoints, protected resource metadata.
- Automatically rolls back the tunnel and MCP drop-in if external validation fails.
- DOES NOT modify PostgreSQL tables or the MultiTool plugin config or old services.

Quick Tunnel URLs are ephemeral and may change. Use this for a short end-to-end ChatGPT connector test, not as a permanent production URL. For reliable production, replace it with a named tunnel on a domain you control or another stable HTTPS ingress.

Security: The unauthenticated /ping endpoint remains public; the actual MCP endpoint is OAuth-protected. The output URL is not a password; the OAuth owner password must never be pasted into agent reports or chat. This exposes MCP OAuth registration/login to the internet; the owner must approve using a third-party tunnel before running.

To explicitly revert the temporary tunnel while leaving PostgreSQL, Taskboard,
MultiTool plugin and the MCP service intact, run 24_disable_public_https.sh.
It validates the exact drop-in it originally wrote and refuses to delete a
changed configuration. It also confirms OAuth issuer returns to localhost.

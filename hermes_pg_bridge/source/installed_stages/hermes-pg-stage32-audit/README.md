# Bridge v2 — Stage 32: read-only privilege and service inventory

Purpose: inventory the existing PostgreSQL role model before designing a **separate ChatGPT identity and agent identity**. The current `bridge_app` role can execute SECURITY DEFINER procedures on behalf of both senders. This is unsuitable as the long-term security boundary even though caller-side checks currently exist.

This package **does not** create roles, change passwords, make SQL mutations, touch OAuth, switch tunnels or write to message tables. It reads service status and deployed source file SHA256 prefixes, and queries PostgreSQL catalog metadata plus aggregate canary delivery counts. It never prints message bodies, access tokens, DB connection strings, IPs or passwords.

The transaction is explicitly **READ ONLY** and queries are a fixed list, not caller-supplied. The script refuses non-`kali` execution. Do not execute as root.

Expected output: JSON inventory, then `STAGE32_READ_ONLY_SECURITY_INVENTORY_OK`, `STAGE32_NO_DATABASE_WRITES_PERFORMED`, `STAGE32_AUDIT_ONLY_NO_SERVICE_OR_SCHEMA_CHANGES`.

This is an *audit*, not an authentication fix. Following review, the next ChatGPT-authored stage will split connection credentials and function execute privileges for the ChatGPT-side MCP and MT worker, with tests and a separate rollback plan. Existing canary should remain unchanged until independently verified.

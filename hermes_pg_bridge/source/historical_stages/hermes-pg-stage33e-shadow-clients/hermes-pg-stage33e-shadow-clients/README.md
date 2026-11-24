# Hermes PG v2 — Stage 33E: shadow clients, NO cutover

This package is authored by ChatGPT to inspect and prepare the actual ChatGPT and
MultiTool entry points **without** changing the live Secure MCP Tunnel, canary,
PostgreSQL schema, active plug-ins, or existing MCP.

What it does:

1. Read-only preflight: checks pinned Stage 33C actor adapter, bridge_chatgpt and
   bridge_agent real logins through persistent SSH port 15433. The existing
   `.pgpass` files still encode 15432; the pinned adapter deliberately parses
   that known format and connects through port 15433 directly. It does not
   require or rewrite libpq `.pgpass` matching rules.
2. Make a separate copy of the PG Inbox plug-in in the package directory. Its
   subprocess uses **only** the `bridge_agent` actor CLI. This is a **candidate**:
   `dryRun` is hard-coded TRUE in JS, its task allowlist is hard-coded to the
   existing successful canary, the CLI denies ALL writes/claims/ACKs, and the
   plug-in is **not added to `opencode.json`**. It **must not** execute SDK tasks.
3. Launch a standalone, loopback-only, read-only MCP service on port 18772 using
   **only** the `bridge_chatgpt` adapter and exposing two limited tools,
   `shadow_actor_health` and `shadow_canary_receipt`, with no message bodies.
   The service has NO OAuth, so do NOT forward it to the existing tunnel or
   any network interface. This is a local integration prototype only.
4. Verify DB real identity, completed canary status, tool/schema compatibility,
   forced dry-run, unchanged existing plug-ins, and localhost HTTP health.

These steps **do not** prove authorization for a general ChatGPT writable MCP,
actual MultiTool SDK delivery under the new role, or OS-level credential isolation.
The current Linux user `kali` still owns both actor secrets. Those remain blockers
for production. The `bridge_app` role must retain its existing privileges for now.

No changes are made to the Stage 33D closed test dialogue or its DB receipts.
Never re-run the one-shot installer after success/failure; use `37_verify_only.sh`
for repeated read-only checking, and `37_disable_shadow_clients.sh` to shut down
only the new shadow MCP. The service is started, but NOT enabled for autostart.

## Execute as Kali

```bash
sha256sum -c SHA256SUMS
python3 -m unittest -v test_stage33e
for f in *.sh; do bash -n "$f"; done
bash 37_preflight.sh
bash 37_install_shadow_clients.sh
bash 37_verify_only.sh
```

The service cannot be queried from ChatGPT directly during this stage. The
current `hermes-pg-v2` Secure Tunnel continues to serve the existing bounded
Stage 31 one-shot canary unchanged.

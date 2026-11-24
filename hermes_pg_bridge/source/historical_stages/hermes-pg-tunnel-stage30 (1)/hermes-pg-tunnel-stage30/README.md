# Stage 30 — Secure MCP Tunnel → read-only real PostgreSQL test

The previous `pgtunnel` stub call succeeded in ordinary ChatGPT, validating the channel.
This stage switches the *same tunnel ID* to a distinct private, read-only
Streamable HTTP MCP on Kali, port 18767. It verifies Store().health() and the
fixed earlier task `task_f2c64cfdf7894537b35a19a6def977c6`, without
exposing bodies, arbitrary task selection, SQL, or write tools.

The MCP server on port 8766 **is not modified**. Its OAuth remains intact.
The new probe has no OAuth because it exposes only bounded, fixed
non-sensitive proof values. **Do not add private reads or writes here.**

One `tunnel-client` runtime per tunnel: after local probe readiness the installer
stops `hermes-secure-tunnel-canary.service` and starts its own runtime.
On failure it stops the new runtime/probe and restarts the original stub.
It does NOT change PostgreSQL, Taskboard, cloudflared or MultiTool.

Run on Kali as `kali`:

```sh
python3 test_stage30.py
bash -n 30_activate_pg_probe.sh 31_rollback.sh 32_status.sh run_pg_tunnel.sh
bash 30_activate_pg_probe.sh
bash 32_status.sh
```

Then in the ordinary ChatGPT UI, reload/reconnect the custom MCP plugin
bound to `hermes-pg-v2` to refresh the discovered tools. If necessary create a
*second* custom MCP plugin pointing at the same existing Tunnel, Auth None,
name `Hermes PG Readonly Probe`; do not create another tunnel or API key.
Ask ChatGPT to call `pg_health` and `pg_canary_receipt`.

Rollback (user authorized):

```sh
bash 31_rollback.sh
```

Private README and scripts contain no user API key. The API key is read by the
service from `/home/kali/.config/hermes-secure-tunnel-canary/runtime.key`.

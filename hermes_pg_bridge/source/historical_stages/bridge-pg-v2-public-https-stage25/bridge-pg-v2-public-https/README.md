# PG Bridge v2: Cloudflare Quick Tunnel systemd dependency hotfix (Stage 25)

Authored by ChatGPT, 2026-11-22. Independent retry of previously rolled-back Stage 23.

## Stage 25 one-line fix

Switch tunnel systemd unit `Requires=bridge-pg-v2-mcp.service` to `Wants=bridge-pg-v2-mcp.service`; retain `After=network.target bridge-pg-v2-mcp.service`. MCP restart must not tear down the tunnel / rotate its PID and URL. The Stage24 PID binding, HTTP/2 and auto-rollback remain unchanged.

## What this version changes

1. Still forces `--protocol http2` (TCP) with `Restart=no` and a loopback origin.
2. Reads structured `journalctl -o json` events rather than taking the last URL from unstructured logs.
3. Requires URL and `Registered tunnel connection ... protocol=http2` from the **same** current `systemctl --user show ... MainPID`. It rejects stale URLs from processes already exited, and detects process replacement during public verification.
4. Waits up to 110 s for registered HTTP/2 connection and up to 120 s more for real external `/ping` 200; never treats mere URL assignment or registration as success.
5. On failure, prints safe HTTP status/error class and rolls back temporary tunnel and MCP base; does not touch the PostgreSQL database, Taskboard, MT plugins, or legacy services.

The public endpoint is temporary. It is not a production replacement for a stable hostname and managed authenticated tunnel. Do not disclose the OAuth password; printing the temporary `PUBLIC_MCP_URL` is allowed.

## Run

Execute from the extracted directory as `kali` with existing, functioning services and no active quick tunnel:

```bash
python3 -m py_compile 23_public_https.py test_stage23.py
python3 test_stage23.py
bash -n 23_launch_public_https.sh
bash -n 23_status_public_https.sh
bash -n 24_disable_public_https.sh
bash 23_launch_public_https.sh
bash 23_status_public_https.sh
```

Do **not** rerun `23_launch_public_https.sh` if it has succeeded; it refuses to overwrite state. If it fails, don't retry automatically: inspect the safe diagnostic first. Rollback is automated on failure. To intentionally disable the temporary public URL later, run `24_disable_public_https.sh` once.

Expected main markers: `STAGE23_OFFLINE_SAFETY_TESTS_OK`, `STAGE24_PID_BOUND_HTTP2_EDGE_READY_TESTS_OK`, `STAGE25_SYSTEMD_DEPENDENCY_NON_PROPAGATING_OK`, `HTTP2_CURRENT_PROCESS_REGISTERED_ON_EDGE`, `EXTERNAL_HTTPS_PING_200_OK`, `EXTERNAL_MCP_401_OAUTH_CHALLENGE_OK`, `EXTERNAL_OAUTH_ISSUER_AND_RESOURCE_METADATA_OK`, `PGV2_PUBLIC_HTTPS_TEST_READY`.

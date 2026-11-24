# Bridge PG v2 — Stage 27 (independent cloudflared ingress)

Fixes the confirmed cause of empty HTTP 404 from Cloudflare: `cloudflared`
used the legacy `~/.cloudflared/config.yml` with a hostname-specific route to
port 8765 and a catch-all `http_status:404`. This package never edits that file.

The one-shot installer creates private
`~/.config/bridge-pg-v2/quick-tunnel-isolated.yml` with a single fallback
route to `http://127.0.0.1:8766`. The unit explicitly specifies this
configuration with `--config`, validates the ingress before starting, then
uses HTTP/2. The OAuth issuer remains the generated quick-tunnel URL.
Rollback and explicit disable remove ONLY the v2-specific configuration.

The existing Hermes plugin in ChatGPT might be reusable by repointing its
**existing stable public hostname** to the new MCP 8766. That is a separate
cutover, requiring confirmed stable public routing and OAuth reauthorization.
Run the optional `27_old_hermes_endpoint_probe.py` to determine the current
public URL reachability. It makes only HTTP GETs without credentials and does
not modify the old tunnel, plugin, or Git bridge.

For the test run: `python3 test_stage23.py` then
`bash 23_launch_public_https.sh`, `bash 23_status_public_https.sh`.
Do not switch plugins yet. The Quick Tunnel URL is not persistent.

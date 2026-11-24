#!/usr/bin/env bash
set -Eeuo pipefail
set +x
for s in hermes-pg-tunnel-one-shot.service hermes-pg-one-shot-canary.service hermes-pg-tunnel-probe.service bridge-pg-v2-mcp.service bridge-pg-v2-taskboard.service; do
  printf '%s=%s\n' "$s" "$(systemctl --user is-active "$s" || true)"
done
for spec in 18769:ping 18770:readyz 18768:readyz; do
  port="${spec%%:*}"; path="${spec#*:}"
  code="$(curl -sS --noproxy '*' --max-time 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/$path" 2>/dev/null || true)"
  printf 'HTTP_%s_%s=%s\n' "$port" "$path" "$code"
done

#!/usr/bin/env bash
set -Eeuo pipefail
set +x
printf 'PG_READONLY_SERVER=%s\n' "$(systemctl --user is-active hermes-pg-readonly-probe.service || true)"
printf 'PG_TUNNEL_RUNTIME=%s\n' "$(systemctl --user is-active hermes-pg-tunnel-probe.service || true)"
printf 'OLD_DEMO=%s\n' "$(systemctl --user is-active hermes-secure-tunnel-canary.service || true)"
for spec in '18767:ping' '18768:readyz' '18768:health/control-plane'; do
  port="${spec%%:*}"; path="${spec#*:}"
  status="$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port/$path" 2>/dev/null || true)"
  printf 'HTTP_%s_%s=%s\n' "$port" "${path//\//_}" "$status"
done

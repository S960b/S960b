#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35b-issuer-readiness
[[ "$(id -un)" == kali ]] || exit 2
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE35B_STOP_NO_PINNED_SSH_TARGET; exit 2; }
[[ -f /home/kali/.ssh/mt_tun_ed25519 ]] || exit 2
# No persistent changes. This checks VPS readiness without exposing its address,
# user configuration, secret files, or command-line arguments.
ssh -i /home/kali/.ssh/mt_tun_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=7 "$PG_V2_SSH_TARGET" 'sh -s' <<'REMOTE'
set -eu
printf 'STAGE35B_VPS_REMOTE_READONLY_ACCESS_OK\n'
if command -v podman >/dev/null 2>&1; then echo STAGE35B_CONTAINER_RUNTIME_PODMAN_PRESENT;
elif command -v docker >/dev/null 2>&1; then echo STAGE35B_CONTAINER_RUNTIME_DOCKER_PRESENT;
else echo STAGE35B_CONTAINER_RUNTIME_NOT_INSTALLED; fi
if command -v caddy >/dev/null 2>&1; then echo STAGE35B_REVERSE_PROXY_CADDY_PRESENT;
elif command -v nginx >/dev/null 2>&1; then echo STAGE35B_REVERSE_PROXY_NGINX_PRESENT;
else echo STAGE35B_REVERSE_PROXY_NOT_DETECTED; fi
if command -v openssl >/dev/null 2>&1; then echo STAGE35B_OPENSSL_PRESENT; fi
if command -v getent >/dev/null 2>&1; then echo STAGE35B_DNS_UTILITIES_PRESENT; fi
if command -v ss >/dev/null 2>&1; then
  # Never print IPs or service command lines; only whether port appears bound.
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:|\])443$'; then
    echo STAGE35B_HTTPS_PORT_LISTENER_PRESENT;
  else echo STAGE35B_HTTPS_PORT_LISTENER_NOT_OBSERVED; fi
fi
if command -v free >/dev/null 2>&1; then
  avail=$(free -m | awk '/^Mem:/{print $7}')
  if [ "${avail:-0}" -ge 2048 ]; then echo STAGE35B_MEMORY_AVAILABLE_AT_LEAST_2048MB;
  else echo STAGE35B_MEMORY_AVAILABLE_BELOW_2048MB; fi
fi
if command -v df >/dev/null 2>&1; then
  disk=$(df -Pm / | awk 'NR==2{print $4}')
  if [ "${disk:-0}" -ge 8192 ]; then echo STAGE35B_DISK_FREE_AT_LEAST_8GB;
  else echo STAGE35B_DISK_FREE_BELOW_8GB; fi
fi
echo STAGE35B_VPS_READINESS_PROBED_NO_CHANGES
REMOTE
echo STAGE35B_SSH_READONLY_AUDIT_FINISHED

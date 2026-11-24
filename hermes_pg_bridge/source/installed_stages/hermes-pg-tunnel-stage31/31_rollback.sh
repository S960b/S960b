#!/usr/bin/env bash
# Restore the prior read-only tunnel runtime without changing DB or MT config.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STOP_wrong_user; exit 2; }
SVC1=hermes-pg-one-shot-canary.service
SVC2=hermes-pg-tunnel-one-shot.service
OLD=hermes-pg-tunnel-probe.service
UNITS=/home/kali/.config/systemd/user
systemctl --user stop "$SVC2" 2>/dev/null || true
systemctl --user stop "$SVC1" 2>/dev/null || true
rm -f -- "$UNITS/$SVC2" "$UNITS/$SVC1"
systemctl --user daemon-reload
systemctl --user reset-failed "$SVC1" "$SVC2" 2>/dev/null || true
systemctl --user start "$OLD"
code=000
for _ in $(seq 1 45); do
  code="$(curl -sS --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:18768/readyz 2>/dev/null || true)"
  [[ "$code" == 200 ]] && { echo STAGE30_READONLY_TUNNEL_RESTORED_200; exit 0; }
  sleep 1
done
echo STOP_readonly_tunnel_restore_failed >&2
exit 1

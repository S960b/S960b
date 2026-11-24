#!/usr/bin/env bash
# Stop only new read-only probe and runtime; restore prior demo runtime.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STOP_wrong_user; exit 2; }
DIR=/home/kali/.config/systemd/user
systemctl --user stop hermes-pg-tunnel-probe.service 2>/dev/null || true
systemctl --user stop hermes-pg-readonly-probe.service 2>/dev/null || true
rm -f -- "$DIR/hermes-pg-tunnel-probe.service" "$DIR/hermes-pg-readonly-probe.service"
systemctl --user daemon-reload
systemctl --user start hermes-secure-tunnel-canary.service
for i in $(seq 1 35); do
  s="$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:18766/readyz 2>/dev/null || true)"
  [[ "$s" == 200 ]] && { echo OLD_STUB_RESTORED_200; exit 0; }
  sleep 1
done
echo ERROR_stub_restore_failed >&2
exit 1

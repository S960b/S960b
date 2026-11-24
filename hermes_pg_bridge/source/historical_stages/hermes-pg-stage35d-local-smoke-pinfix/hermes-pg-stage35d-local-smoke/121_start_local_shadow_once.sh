#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35d-local-smoke
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ "$(id -un)" == kali ]] || { echo STAGE35D_STOP_NOT_KALI;exit 2; }
[[ ! -e local-shadow-start-attempt.json && ! -L local-shadow-start-attempt.json ]] || { echo STAGE35D_STOP_ALREADY_ATTEMPTED;exit 2; }
env -u APPDIR -u APPIMAGE "$PY" stage35d.py preflight
printf '{"stage":"35D","local_service_start_attempted":true,"tunnel_switched":false}\n' > local-shadow-start-attempt.json
chmod 600 local-shadow-start-attempt.json
# This starts ONLY the local loopback service. No tunnel, schema, plugin or SDK changes.
systemctl --user start hermes-pg-stage35c-static-shadow.service
ok=0
for _ in $(seq 1 20); do
  if curl --noproxy '*' --silent --max-time 2 http://127.0.0.1:18777/ping | grep -q 'stage35c-static-shadow'; then ok=1;break;fi
  sleep 1
done
if ((ok != 1)); then
  systemctl --user stop hermes-pg-stage35c-static-shadow.service || true
  echo STAGE35D_STOP_LOCAL_SHADOW_FAILED_TO_START
  exit 2
fi
if ! env -u APPDIR -u APPIMAGE "$PY" stage35d.py verify-live; then
  systemctl --user stop hermes-pg-stage35c-static-shadow.service || true
  echo STAGE35D_STOP_LOCAL_SHADOW_PROBE_FAILED_SERVICE_STOPPED
  exit 2
fi
echo STAGE35D_LOCAL_SHADOW_STARTED_VERIFIED_TUNNEL_UNCHANGED

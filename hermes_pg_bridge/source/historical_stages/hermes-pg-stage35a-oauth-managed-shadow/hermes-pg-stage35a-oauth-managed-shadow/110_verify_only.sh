#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
cd /home/kali/hermes-pg-stage35a-oauth-managed-shadow
PY=/home/kali/bridge-pg-v2/.venv/bin/python
env -u APPDIR -u APPIMAGE "$PY" stage35a.py verify
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-v2-stage33fb-cg.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-stage35a-oauth-shadow.service 2>/dev/null || true)" != active ]] || exit 2
echo STAGE35A_EXISTING_TUNNEL_AND_CANARY_SERVICES_UNCHANGED
echo STAGE35A_VERIFY_COMPLETE_NO_REGISTRATION_NO_CUTOVER_NO_SQL_WRITES

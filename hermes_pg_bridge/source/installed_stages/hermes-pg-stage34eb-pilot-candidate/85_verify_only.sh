#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34eb-pilot-candidate
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" pilot34eb.py verify
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34eb-pilot-unregistered.mjs"
( cd /home/kali/hermes-pg-stage34e-managed-runtime &&
  env -u APPDIR -u APPIMAGE "$PY" verify34e.py verify )
echo STAGE34EB_CANDIDATE_READY_UNREGISTERED_ONE_PILOT_ONLY
echo STAGE34EB_VERIFY_ONLY_NO_DB_NO_SDK_NO_TUNNEL_CUTOVER

#!/usr/bin/env bash
# Safe after arm and after actual send. No send, claim or SDK invocation.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fd-tunnel-cutover
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-v2-stage33fb-cg.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-tunnel-stage33fd.service)" == active ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python - <<'PY'
import sys,json
sys.path[:0]=['/home/kali/hermes-pg-stage33fb-mcp-ready','/home/kali/hermes-pg-stage33e-shadow-clients']
from actor_import33e import actor_classes
from logic33fb import status,is_armed
C,A=actor_classes()
r=status(C())
print(json.dumps({'stage':'33F-D','write_armed':is_armed(),**r},sort_keys=True))
print('STAGE33FD_READ_ONLY_RECEIPT_CHECKED')
PY

#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34d-write-adapter
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE34D_STOP_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
env -u APPDIR -u APPIMAGE "$PY" probe34d.py preflight
env -u APPDIR -u APPIMAGE "$PY" render34d.py
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage34d-shadow.mjs"
# Even the new write-capable agent CLI has NO authorization to write now.
set +e
OUT=$(env -u APPDIR -u APPIMAGE "$PY" managed_agent_cli34d.py claim --task-id task_f2c64cfdf7894537b35a19a6def977c6 --message-id blocked-proof --seq 999 --consumer mt-pg-managed 2>/dev/null)
RC=$?
set -e
[[ $RC -eq 2 && "$OUT" == *'stage34d_writes_not_armed'* ]] || { echo STAGE34D_STOP_WRITE_DENIAL_TEST >&2; exit 2; }
echo STAGE34D_WRITE_GATE_DENIED_WITHOUT_SQL_MUTATION
bash 78_verify_only.sh
printf '%s\n' STAGE34D_PREPARE_COMPLETE_NO_CUTOVER

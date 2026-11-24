#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SERVICE=bridge-pg-v2-taskboard.service
BACKUP=''
STOP() { echo "STAGE40_STOP_$1" >&2; exit 2; }

# Verify the offline package tests before ANY live mutations.
python3 -m unittest discover -s "$DIR" -p 'test_stage40.py' -q || STOP OFFLINE_TESTS
printf 'STAGE40_OFFLINE_TESTS_OK\n'
[ "$(id -un)" = kali ] || STOP WRONG_USER
systemctl --user is-active --quiet "$SERVICE" || STOP TASKBOARD_SERVICE_NOT_ACTIVE
python3 - <<'PY' || exit 2
import urllib.request
from urllib.error import HTTPError
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    opener.open('http://127.0.0.1:8789/api/dialogs?offset=0&limit=30',timeout=3)
    raise RuntimeError('unexpected unauthenticated access')
except HTTPError as e:
    if e.code != 401: raise RuntimeError('auth baseline changed')
print('STAGE40_BEFORE_AUTH_401_OK')
PY

# Source SHA pins, semantic anchors, AST and node syntax validated before install.
INSTALL_OUTPUT=$(python3 -I "$DIR/stage40_patch.py" install) || STOP PATCH_PREFLIGHT_OR_INSTALL
printf '%s\n' "$INSTALL_OUTPUT"
BACKUP=$(printf '%s\n' "$INSTALL_OUTPUT" | sed -n 's/^STAGE40_RESTORE_DIR //p')
[ -n "$BACKUP" ] || STOP MISSING_BACKUP_DIR

if ! timeout 35 systemctl --user restart "$SERVICE"; then
    echo 'STAGE40_RESTART_FAILED_ATTEMPTING_ROLLBACK' >&2
    python3 -I "$DIR/stage40_patch.py" restore "$BACKUP" || STOP ROLLBACK_FAILED
    timeout 35 systemctl --user restart "$SERVICE" || STOP ROLLBACK_SERVICE_RESTART_FAILED
    STOP SERVICE_RESTART
fi
if ! systemctl --user is-active --quiet "$SERVICE"; then
    echo 'STAGE40_SERVICE_NOT_ACTIVE_ATTEMPTING_ROLLBACK' >&2
    python3 -I "$DIR/stage40_patch.py" restore "$BACKUP" || STOP ROLLBACK_FAILED
    timeout 35 systemctl --user restart "$SERVICE" || STOP ROLLBACK_SERVICE_RESTART_FAILED
    STOP SERVICE_NOT_ACTIVE
fi

# No credentials are read, printed or transmitted; retain existing Basic Auth boundary.
if ! python3 - <<'PY'
import urllib.request
from urllib.error import HTTPError
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    opener.open('http://127.0.0.1:8789/api/dialogs?offset=0&limit=30',timeout=3)
    raise RuntimeError('unauthenticated endpoint became accessible')
except HTTPError as e:
    if e.code != 401: raise RuntimeError(f'auth regression HTTP {e.code}')
print('STAGE40_AFTER_AUTH_401_OK')
PY
then
    echo 'STAGE40_AUTH_CHECK_FAILED_ATTEMPTING_ROLLBACK' >&2
    python3 -I "$DIR/stage40_patch.py" restore "$BACKUP" || STOP ROLLBACK_FAILED
    timeout 35 systemctl --user restart "$SERVICE" || STOP ROLLBACK_SERVICE_RESTART_FAILED
    STOP AUTH_CHECK
fi

printf 'STAGE40_TASKBOARD_8789_ACTIVE\n'
printf 'STAGE40_MCP_PG_AND_DATABASE_UNCHANGED\n'
printf 'STAGE40_NEXT_PERFORMANCE_CHECK_WITH_AUTH_IN_BROWSER\n'
printf 'STAGE40_BACKUP_DIR %s\n' "$BACKUP"

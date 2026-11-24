#!/usr/bin/env bash
# One command: verified install -> local-only E2E -> automated rollback-capable cutover.
set -Eeuo pipefail
set +x
umask 077
stop(){ echo "STAGE35JK_INSTALL_STOP_$1" >&2; exit 2; }
[[ $(id -un) == kali && $HOME == /home/kali && $EUID != 0 ]] || stop WRONG_USER
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ $(basename "$SRC") == hermes-pg-stage35jk-cutover ]] || stop INVALID_SOURCE_DIR
TARGET=/home/kali/hermes-pg-stage35jk-cutover
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ -x $PY ]] || stop MISSING_VENV
[[ ! -e $TARGET && ! -L $TARGET ]] || stop TARGET_EXISTS_DO_NOT_REINSTALL
(
 cd "$SRC"
 python3 - <<'PY'
from pathlib import Path
import stat
expect={'stage35jk.py','test_stage35jk.py','README.md','SHA256SUMS',
        '180_install_verify_and_cutover_once.sh','181_verify_only.sh',
        '182_rollback_if_needed.sh','run_managed_tunnel.sh'}
assert {p.name for p in Path('.').iterdir()}==expect
assert all(stat.S_ISREG(p.lstat().st_mode) and p.lstat().st_nlink==1 for p in Path('.').iterdir())
PY
 sha256sum -c SHA256SUMS >/dev/null
 for f in *.sh; do bash -n "$f"; done
 PYTHONDONTWRITEBYTECODE=1 "$PY" -B -m unittest -q test_stage35jk
 PYTHONDONTWRITEBYTECODE=1 "$PY" -B - <<'PY'
import ast
from pathlib import Path
for path in Path('.').glob('*.py'):ast.parse(path.read_text('utf8'),filename=str(path))
print('STAGE35JK_AST_ALL_SOURCE_OK')
PY
) || stop ARCHIVE_MANIFEST_OR_TESTS_FAILED
printf 'STAGE35JK_INSTALL_MANIFEST_SHA256_AND_OFFLINE_TESTS_OK\n'
chmod 700 "$SRC"
for f in "$SRC"/*; do chmod 600 "$f"; done
chmod 700 "$SRC/run_managed_tunnel.sh"
mv -- "$SRC" "$TARGET" || stop INSTALL_MOVE_FAILED
cd "$TARGET"
printf 'STAGE35JK_INSTALLED_ISOLATED_OLD_TUNNEL_UNCHANGED\n'
PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35jk.py preflight || stop PREFLIGHT_FAILED_NO_CHANGES
PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35jk.py local-once || stop LOCAL_E2E_FAILED_NO_CUTOVER
printf 'STAGE35JK_LOCAL_ONLY_GATE_SUCCESS_ENTERING_ROLLBACK_GUARDED_CUTOVER\n'
PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35jk.py cutover-once || stop CUTOVER_FAILED_CHECK_ROLLBACK_MARKERS
printf 'STAGE35JK_CUTOVER_LOCAL_READY_REMOTE_CHATGPT_PLUGIN_CHECK_REQUIRED\n'

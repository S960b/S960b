#!/usr/bin/env bash
# ChatGPT-authored; initial install + guarded recovery. Do not replay on failure.
set -Eeuo pipefail
set +x
umask 077
fail(){ printf 'STAGE35L_INSTALL_STOP_%s\n' "$1" >&2; exit 2; }
[[ $(id -un) == kali && $HOME == /home/kali && $EUID != 0 ]] || fail WRONG_USER
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ $(basename "$SRC") == hermes-pg-stage35l-recovery-cutover ]] || fail SOURCE_DIRECTORY_INVALID
TARGET=/home/kali/hermes-pg-stage35l-recovery-cutover
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ -x "$PY" ]] || fail PYTHON_VENV_NOT_FOUND
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] || fail TARGET_ALREADY_EXISTS
(
  cd "$SRC"
  python3 - <<'PY'
from pathlib import Path
import stat
expect={'stage35l.py','test_stage35l.py','190_install_and_resume_cutover_once.sh',
        '191_verify_only.sh','192_owner_rollback.sh','README.md','SHA256SUMS'}
present=list(Path('.').iterdir())
assert {x.name for x in present}==expect
assert all(stat.S_ISREG(x.lstat().st_mode) and x.lstat().st_nlink==1 for x in present)
PY
  sha256sum -c SHA256SUMS >/dev/null
  for f in ./*.sh; do bash -n "$f"; done
  PYTHONDONTWRITEBYTECODE=1 "$PY" -B -m unittest -q test_stage35l
  PYTHONDONTWRITEBYTECODE=1 "$PY" -B - <<'PY'
import ast
from pathlib import Path
for x in Path('.').glob('*.py'):ast.parse(x.read_text('utf8'))
print('STAGE35L_AST_ALL_PYTHON_OK')
PY
) || fail PACKAGE_MANIFEST_OR_OFFLINE_TESTS_FAILED
printf 'STAGE35L_PACKAGE_MANIFEST_TESTS_AND_AST_OK\n'
chmod 700 "$SRC"
for f in "$SRC"/*; do chmod 600 "$f"; done
chmod 700 "$SRC"/*.sh
mv -- "$SRC" "$TARGET" || fail INSTALL_FAILED
cd "$TARGET"
printf 'STAGE35L_INSTALLED_PRIVATE_35JK_35G_AND_TUNNEL_UNCHANGED\n'
PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35l.py cutover-once || fail CUTOVER_FAILED_CHECK_ROLLBACK_MARKERS
PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35l.py verify || fail POSTCUTOVER_VERIFY_FAILED_CHECK_ROLLBACK
printf 'STAGE35L_INSTALL_COMPLETE_NEW_TUNNEL_ACTIVE_REMOTE_CHECK_REQUIRED\n'

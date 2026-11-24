#!/usr/bin/env bash
# Stage35H + I. Single run: verify archive, unit, actual local dev-proxy, cold candidate.
set -Eeuo pipefail
set +x
umask 077
stop(){ printf 'STAGE35HI_INSTALL_STOP_%s\n' "$1" >&2; exit 2; }
[[ $(id -un) == kali && $HOME == /home/kali && $(id -u) != 0 ]] || stop BAD_USER
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ $(basename "$SRC") == hermes-pg-stage35hi-local-e2e ]] || stop BAD_SOURCE_NAME
TARGET=/home/kali/hermes-pg-stage35hi-local-e2e
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ -x $PY ]] || stop PYTHON_VENV_UNAVAILABLE
[[ ! -e $TARGET && ! -L $TARGET ]] || stop TARGET_ALREADY_EXISTS_DO_NOT_REINSTALL
(
 cd "$SRC"
 python3 - <<'PY'
from pathlib import Path
import stat
wanted={'proxy35hi.py','test_stage35hi.py','README.md','SHA256SUMS',
        '170_install_and_run_once.sh','run_stage35hi_candidate_NOT_ACTIVATED.sh'}
assert {p.name for p in Path('.').iterdir()}==wanted
assert all(stat.S_ISREG(p.lstat().st_mode) and p.lstat().st_nlink==1
           for p in Path('.').iterdir())
PY
 sha256sum -c SHA256SUMS >/dev/null
 bash -n 170_install_and_run_once.sh
 bash -n run_stage35hi_candidate_NOT_ACTIVATED.sh
 env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B -m unittest -q test_stage35hi
 env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B - <<'PY'
import ast
from pathlib import Path
for p in Path('.').glob('*.py'):ast.parse(p.read_text(),filename=str(p))
print('STAGE35HI_AST_ALL_SOURCES_OK',flush=True)
PY
) || stop MANIFEST_OR_OFFLINE_TESTS_FAILED
printf 'STAGE35HI_PACKAGE_MANIFEST_AND_OFFLINE_TESTS_OK\n'
chmod 700 "$SRC"
for file in "$SRC"/*; do chmod 600 "$file"; done
mv -- "$SRC" "$TARGET" || stop INSTALL_MOVE_FAILED
printf 'STAGE35HI_INSTALLED_PRIVATE_NO_OLD_TUNNEL_OR_PLUGIN_MUTATION\n'
cd "$TARGET"
env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B proxy35hi.py preflight || stop PREFLIGHT_FAILED
printf 'STAGE35HI_READONLY_PREFLIGHT_OK\n'
env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B proxy35hi.py once || stop LOCAL_DEV_PROXY_FAILED_DO_NOT_RETRY
printf 'STAGE35HI_PACKAGE_COMPLETED_READY_FOR_SEPARATE_REVIEWED_CUTOVER\n'

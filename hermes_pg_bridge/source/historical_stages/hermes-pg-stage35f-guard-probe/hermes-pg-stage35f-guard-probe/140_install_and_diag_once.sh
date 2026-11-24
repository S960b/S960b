#!/usr/bin/env bash
# Stage35F installer, extracted only after user verifies SHA256 of one ZIP.
set -Eeuo pipefail
set +x
umask 077
stop(){ printf 'STAGE35F_INSTALL_STOP_%s\n' "$1" >&2; exit 2; }
[[ $(id -un) == kali && "$HOME" == /home/kali && $(id -u) != 0 ]] || stop WRONG_USER
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
TARGET=/home/kali/hermes-pg-stage35f-guard-probe
[[ "$(basename "$SRC")" == hermes-pg-stage35f-guard-probe ]] || stop BAD_SOURCE
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] || stop TARGET_EXISTS_NO_REINSTALL
[[ -f "$SRC/SHA256SUMS" && ! -L "$SRC/SHA256SUMS" ]] || stop MANIFEST_MISSING
[[ -x /home/kali/bridge-pg-v2/.venv/bin/python ]] || stop PYTHON_MISSING
(
 cd "$SRC"
 python3 - <<'PY'
import pathlib,stat
root=pathlib.Path('.')
allowed={'guard35f.py','stream35f.py','diag_server35f.py','stage35f.py',
         'test_stage35f.py','log_audit35f.py','README.md','SHA256SUMS','140_install_and_diag_once.sh'}
seen={p.name for p in root.iterdir()}
assert seen==allowed,(seen-allowed,allowed-seen)
for p in root.iterdir():
 s=p.lstat()
 assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and not p.is_symlink(), p.name
PY
 sha256sum -c SHA256SUMS >/dev/null
 bash -n 140_install_and_diag_once.sh
 /home/kali/bridge-pg-v2/.venv/bin/python -B -m unittest -q test_stage35f
 /home/kali/bridge-pg-v2/.venv/bin/python -B - <<'PY'
from pathlib import Path
import ast
for p in Path('.').glob('*.py'):ast.parse(p.read_text(),filename=str(p))
print('STAGE35F_AST_ALL_SOURCES_OK')
PY
) || stop OFFLINE_VERIFICATION_FAILED
printf 'STAGE35F_INSTALL_MANIFEST_AND_OFFLINE_TESTS_OK\n'
chmod -R go-rwx "$SRC"
mv -- "$SRC" "$TARGET" || stop ATOMIC_INSTALL_FAILED
printf 'STAGE35F_INSTALLED_ISOLATED_NO_ORIGINAL_CODE_OR_SYSTEMD_CHANGES\n'
cd "$TARGET"
/home/kali/bridge-pg-v2/.venv/bin/python -B stage35f.py preflight || stop PRECHECK_FAILED
printf 'STAGE35F_PREFLIGHT_OK_RUNNING_NEW_ONCE_ONLY\n'
set +e
/home/kali/bridge-pg-v2/.venv/bin/python -B stage35f.py once
RC=$?
set -e
/home/kali/bridge-pg-v2/.venv/bin/python -B log_audit35f.py || true
if [[ $RC -ne 0 ]]; then stop DIAGNOSTIC_FAILED_DO_NOT_RETRY; fi
printf 'STAGE35F_INSTALL_DIAGNOSTIC_SUCCESS_NO_REPLAY\n'

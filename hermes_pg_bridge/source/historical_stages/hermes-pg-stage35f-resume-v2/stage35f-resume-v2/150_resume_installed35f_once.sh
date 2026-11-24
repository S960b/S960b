#!/usr/bin/env bash
# Stage35F resumed after a failed *preflight*. No 140 reinstall and no replay of 35D/35E.
set -Eeuo pipefail
set +x
umask 077
stop(){ printf 'STAGE35F_RESUME_INSTALL_STOP_%s\n' "$1" >&2; exit 2; }
[[ $(id -un) == kali && "$HOME" == /home/kali && $(id -u) != 0 ]] || stop WRONG_USER
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ $(basename "$SRC") == stage35f-resume-v2 ]] || stop BAD_SOURCE
F=/home/kali/hermes-pg-stage35f-guard-probe
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ -x "$PY" && -d "$F" && ! -L "$F" ]] || stop TARGET_OR_PYTHON_MISSING
[[ -f "$SRC/SHA256SUMS" && ! -L "$SRC/SHA256SUMS" ]] || stop PACKAGE_MANIFEST_MISSING
(
 cd "$SRC"
 sha256sum -c SHA256SUMS >/dev/null
 bash -n 150_resume_installed35f_once.sh
 "$PY" -B -m unittest -q test_permission_repair35f
 "$PY" -B - <<'PY'
import ast
from pathlib import Path
for p in Path('.').glob('*.py'):
 ast.parse(p.read_text('utf8'), filename=p.name)
print('STAGE35F_RESUME_PACKAGE_AST_OK')
PY
) || stop PACKAGE_VERIFY_FAILED
printf 'STAGE35F_RESUME_MANIFEST_AND_OFFLINE_TESTS_OK\n'
(
 cd "$F"
 [[ ! -e stage35f-once-attempt.json && ! -L stage35f-once-attempt.json && \
    ! -e stage35f-uvicorn-private.log && ! -L stage35f-uvicorn-private.log ]] || stop ONCE_MARKER_PRESENT_NO_REPLAY
 sha256sum -c SHA256SUMS >/dev/null
) || stop INSTALLED_35F_MANIFEST_OR_MARKER_FAILED
printf 'STAGE35F_RESUME_INSTALLED_ORIGINAL_35F_PINNED_NOT_REINSTALLED\n'
"$PY" -B "$SRC/permission_repair35f.py" apply || stop PINNED_PERMISSION_REPAIR_FAILED
cd "$F"
env -u APPDIR -u APPIMAGE -u PYTHONPATH "$PY" -B stage35f.py preflight || stop ORIGINAL_35F_PREFLIGHT_FAILED
printf 'STAGE35F_RESUME_PREFLIGHT_OK_STARTING_ORIGINAL_ONE_SHOT\n'
set +e
env -u APPDIR -u APPIMAGE -u PYTHONPATH "$PY" -B stage35f.py once
rc=$?
set -e
"$PY" -B log_audit35f.py || true
if [[ $rc -ne 0 ]]; then stop DIAGNOSTIC_FAILED_DO_NOT_RETRY; fi
printf 'STAGE35F_RESUME_DIAGNOSTIC_SUCCESS_NO_TUNNEL_CUTOVER\n'

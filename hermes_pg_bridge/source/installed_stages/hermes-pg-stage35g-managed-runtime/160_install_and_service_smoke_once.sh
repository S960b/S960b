#!/usr/bin/env bash
# Stage35G: only an inactive user-unit drop-in, then ONE systemd local smoke.
# No tunnel registration, no DB writes, no permission broadening, no replay.
set -Eeuo pipefail
set +x
umask 077
stop(){ printf 'STAGE35G_INSTALL_STOP_%s\n' "$1" >&2;exit 2; }
[[ $(id -un) == kali && $HOME == /home/kali && $(id -u) != 0 ]] || stop BAD_USER
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ $(basename -- "$SRC") == hermes-pg-stage35g-managed-runtime ]] || stop BAD_SOURCE_BASENAME
TARGET=/home/kali/hermes-pg-stage35g-managed-runtime
PY=/home/kali/bridge-pg-v2/.venv/bin/python
[[ -x $PY ]] || stop PYTHON_VENV_MISSING
[[ ! -e $TARGET && ! -L $TARGET ]] || stop TARGET_ALREADY_EXISTS
[[ ! -e /home/kali/.config/systemd/user/hermes-pg-stage35c-static-shadow.service.d &&
   ! -L /home/kali/.config/systemd/user/hermes-pg-stage35c-static-shadow.service.d ]] || stop DROPIN_ALREADY_EXISTS
(
 cd "$SRC"
 python3 - <<'PY'
import pathlib,stat
root=pathlib.Path('.')
allowed={'guard35g.py','stream35g.py','service35g.py','stage35g.py','test_stage35g.py',
         'README.md','SHA256SUMS','160_install_and_service_smoke_once.sh'}
seen={p.name for p in root.iterdir()}
assert seen==allowed,(seen-allowed,allowed-seen)
for p in root.iterdir():
 s=p.lstat()
 assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and not p.is_symlink(),p
PY
 sha256sum -c SHA256SUMS >/dev/null
 bash -n 160_install_and_service_smoke_once.sh
 PYTHONDONTWRITEBYTECODE=1 "$PY" -B -m unittest -q test_stage35g
 PYTHONDONTWRITEBYTECODE=1 "$PY" -B - <<'PY'
from pathlib import Path
import ast
for p in Path('.').glob('*.py'):ast.parse(p.read_text(encoding='utf8'),filename=str(p))
print('STAGE35G_ALL_PYTHON_AST_OK')
PY
) || stop INTEGRITY_OR_OFFLINE_TESTS_FAILED
printf 'STAGE35G_PACKAGE_MANIFEST_AND_21_TESTS_OK\n'
# Change modes only inside the freshly extracted, verified package.
chmod 700 "$SRC"
for p in "$SRC"/*; do chmod 600 "$p"; done
mv -- "$SRC" "$TARGET" || stop INSTALL_MOVE_FAILED
printf 'STAGE35G_INSTALLED_ISOLATED_NO_35C_SOURCE_OR_OLD_TUNNEL_MUTATION\n'
cd "$TARGET"
env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35g.py preflight || stop PRECHECK_FAILED
env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35g.py install-dropin || stop DROPIN_INSTALL_FAILED
env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35g.py service-smoke-once || stop SERVICE_SMOKE_FAILED_DO_NOT_RETRY
env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 "$PY" -B stage35g.py client-help || stop CLIENT_HELP_FAILED_NO_TUNNEL_CHANGED
printf 'STAGE35G_COMPLETE_PATCHED_SYSTEMD_SHADOW_VERIFIED_STOPPED_OLD_TUNNEL_UNCHANGED\n'

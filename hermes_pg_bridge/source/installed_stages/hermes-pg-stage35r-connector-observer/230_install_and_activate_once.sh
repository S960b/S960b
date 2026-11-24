#!/usr/bin/env bash
# Stage 35R - install the ChatGPT-auth diagnostics and activate for one attempt.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && $EUID -ne 0 ]] || { echo STAGE35R_STOP_WRONG_USER; exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=/home/kali/hermes-pg-stage35r-connector-observer
[[ ! -e $ROOT && ! -L $ROOT && -d $SRC ]] || { echo STAGE35R_STOP_TARGET_EXISTS_OR_MISSING_SOURCE; exit 2; }
cd "$SRC"
sha256sum -c SHA256SUMS >/dev/null
python3 -m unittest discover -p 'test_*.py' -q
python3 - <<'PY'
import ast,pathlib,sys
for f in sorted(pathlib.Path('.').glob('*.py')):ast.parse(f.read_text(encoding='utf8'), filename=str(f))
print('STAGE35R_PACKAGE_CHECKSUMS_TESTS_AND_AST_OK')
PY
[[ -z $(find "$SRC" -type l -print -quit) ]] || { echo STAGE35R_STOP_PACKAGE_SYMLINK; exit 2; }
# No executable file from the download is executed until checks have passed.
find "$SRC" -type d -name __pycache__ -prune -exec rm -rf -- {} +
chmod 700 "$SRC"
find "$SRC" -maxdepth 1 -type f -exec chmod 600 -- {} +
chmod 700 "$SRC"/run_stage35r_instrumented.sh "$SRC"/230_install_and_activate_once.sh "$SRC"/231_collect_restore.sh "$SRC"/232_emergency_restore.sh
mv -- "$SRC" "$ROOT"
echo STAGE35R_INSTALLED_PRIVATE_ORIGINAL_MCP_AND_PLUGINS_UNCHANGED
cd "$ROOT"
python3 stage35r.py activate

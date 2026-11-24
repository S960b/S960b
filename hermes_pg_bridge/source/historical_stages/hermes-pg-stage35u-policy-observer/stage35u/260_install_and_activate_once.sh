#!/usr/bin/env bash
# ChatGPT Stage35U. Must only be executed by kali; runtime will use a one-shot marker.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && "$EUID" -ne 0 ]] || { echo STAGE35U_STOP_WRONG_USER;exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=/home/kali/hermes-pg-stage35u-connector-observer
[[ ! -e "$ROOT" && ! -L "$ROOT" && -d "$SRC" ]] || { echo STAGE35U_STOP_TARGET_EXISTS_OR_SOURCE_MISSING;exit 2; }
cd "$SRC"
sha256sum -c SHA256SUMS >/dev/null
python3 -m unittest discover -p 'test_*.py' -q
python3 - <<'PY'
import ast,pathlib
for f in pathlib.Path('.').glob('*.py'):
    ast.parse(f.read_text(encoding='utf8'),filename=str(f))
print('STAGE35U_ARCHIVE_HASHES_OFFLINE_TESTS_AST_OK',flush=True)
PY
[[ -z $(find "$SRC" -type l -print -quit) ]] || { echo STAGE35U_STOP_PACKAGE_SYMLINK;exit 2; }
find "$SRC" -type d -name __pycache__ -prune -exec rm -rf -- {} +
chmod 700 "$SRC"
find "$SRC" -maxdepth 1 -type f -exec chmod 600 -- {} +
chmod 700 "$SRC"/run_stage35u_instrumented.sh "$SRC"/260_install_and_activate_once.sh "$SRC"/261_collect_restore.sh "$SRC"/262_emergency_restore.sh
mv -- "$SRC" "$ROOT"
echo STAGE35U_INSTALLED_PRIVATE_35T_RESTORED_DATABASE_UNTOUCHED
cd "$ROOT"
python3 stage35u.py activate

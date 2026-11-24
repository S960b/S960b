#!/usr/bin/env bash
# ChatGPT Stage35T. Must only be executed by kali; runtime will use a one-shot marker.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && "$EUID" -ne 0 ]] || { echo STAGE35T_STOP_WRONG_USER;exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=/home/kali/hermes-pg-stage35t-connector-observer
[[ ! -e "$ROOT" && ! -L "$ROOT" && -d "$SRC" ]] || { echo STAGE35T_STOP_TARGET_EXISTS_OR_SOURCE_MISSING;exit 2; }
cd "$SRC"
sha256sum -c SHA256SUMS >/dev/null
python3 -m unittest discover -p 'test_*.py' -q
python3 - <<'PY'
import ast,pathlib
for f in pathlib.Path('.').glob('*.py'):
    ast.parse(f.read_text(encoding='utf8'),filename=str(f))
print('STAGE35T_ARCHIVE_HASHES_OFFLINE_TESTS_AST_OK',flush=True)
PY
[[ -z $(find "$SRC" -type l -print -quit) ]] || { echo STAGE35T_STOP_PACKAGE_SYMLINK;exit 2; }
find "$SRC" -type d -name __pycache__ -prune -exec rm -rf -- {} +
chmod 700 "$SRC"
find "$SRC" -maxdepth 1 -type f -exec chmod 600 -- {} +
chmod 700 "$SRC"/run_stage35t_instrumented.sh "$SRC"/250_install_and_activate_once.sh "$SRC"/251_collect_restore.sh "$SRC"/252_emergency_restore.sh
mv -- "$SRC" "$ROOT"
echo STAGE35T_INSTALLED_PRIVATE_35S_RESTORED_DATABASE_UNTOUCHED
cd "$ROOT"
python3 stage35t.py activate

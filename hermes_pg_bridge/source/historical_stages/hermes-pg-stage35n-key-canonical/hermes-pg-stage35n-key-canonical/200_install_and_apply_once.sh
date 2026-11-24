#!/usr/bin/env bash
# Stage35N: bounded, local-key canonicalization. Executed by kali only.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && $EUID -ne 0 ]] || { echo STAGE35N_INSTALL_STOP_WRONG_USER; exit 2; }
ROOT=/home/kali/hermes-pg-stage35n-key-canonical
[[ ! -e $ROOT && ! -L $ROOT ]] || { echo STAGE35N_INSTALL_STOP_ALREADY_INSTALLED; exit 2; }
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -d $SOURCE && ! -L $SOURCE && $SOURCE != "$ROOT" ]] || { echo STAGE35N_INSTALL_STOP_UNEXPECTED_PACKAGE; exit 2; }
cd "$SOURCE"
sha256sum -c SHA256SUMS >/dev/null
python3 -m py_compile stage35n.py test_stage35n.py
python3 -m unittest -q test_stage35n
printf '%s\n' STAGE35N_INSTALL_PACKAGE_TESTS_AND_MANIFEST_OK
# Restrict contents to known files, avoid preserving any unexpected archive payload.
python3 - "$SOURCE" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); names={x.name for x in p.iterdir()}
expected={'stage35n.py','test_stage35n.py','200_install_and_apply_once.sh','201_verify_only.sh','README.md','SHA256SUMS','__pycache__'}
assert names<=expected and names>=expected-{'__pycache__'}
assert all(not x.is_symlink() for x in p.iterdir())
PY
find "$SOURCE" -maxdepth 1 -type f -exec chmod 600 {} +
chmod 700 "$SOURCE/200_install_and_apply_once.sh" "$SOURCE/201_verify_only.sh"
[[ -d $SOURCE/__pycache__ ]] && rm -rf -- "$SOURCE/__pycache__"
chmod 700 "$SOURCE"
mv -T -- "$SOURCE" "$ROOT"
cd "$ROOT"
echo STAGE35N_INSTALL_ISOLATED_EXISTING_MCP_AND_PLUGINS_UNCHANGED
python3 stage35n.py apply-once

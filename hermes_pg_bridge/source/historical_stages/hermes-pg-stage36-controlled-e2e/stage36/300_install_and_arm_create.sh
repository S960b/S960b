#!/usr/bin/env bash
# ChatGPT authored. One shot: installs the reviewed permit controller and arms create only.
set -Eeuo pipefail
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali ]] || { echo STAGE36_STOP_WRONG_USER; exit 2; }
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
TARGET=/home/kali/hermes-pg-stage36-controlled-e2e
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] || { echo STAGE36_STOP_EXISTING_INSTALL; exit 2; }
cd "$SRC"
sha256sum -c SHA256SUMS >/dev/null
python3 -m unittest discover -q -p 'test_stage36.py'
python3 - <<'PY'
import ast
from pathlib import Path
for f in Path('.').glob('*.py'):ast.parse(f.read_text('utf8'),filename=str(f))
PY
bash -n ./300_install_and_arm_create.sh ./301_scope_to_created_task.sh ./302_revoke_permit.sh
printf 'STAGE36_OFFLINE_INTEGRITY_AST_TESTS_BASH_OK\n'
/home/kali/bridge-pg-v2/.venv/bin/python -IB "$SRC/stage36.py" preflight
# Do not create the target unless all offline checks and external preflight passed.
install -d -m 0700 "$TARGET"
install -m 0600 "$SRC/stage36.py" "$TARGET/stage36.py"
install -m 0600 "$SRC/test_stage36.py" "$TARGET/test_stage36.py"
install -m 0700 "$SRC/301_scope_to_created_task.sh" "$TARGET/301_scope_to_created_task.sh"
install -m 0700 "$SRC/302_revoke_permit.sh" "$TARGET/302_revoke_permit.sh"
install -m 0600 "$SRC/SHA256SUMS" "$TARGET/SHA256SUMS"
install -m 0600 "$SRC/README.md" "$TARGET/README.md"
/home/kali/bridge-pg-v2/.venv/bin/python -IB "$TARGET/stage36.py" arm-create

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PATH="/home/kali/.hermes/node/bin:/usr/bin:/bin:$PATH"
unset NODE_OPTIONS NODE_PATH
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(id -u)" != 0 ]] || { echo STAGE37B2D_STOP_USER;exit 2; }
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
INSTALL=/home/kali/hermes-pg-stage37b2d-safe-cutover
[[ ! -e "$INSTALL" && ! -L "$INSTALL" ]] || { echo STAGE37B2D_STOP_ALREADY_INSTALLED; exit 2; }
cd "$HERE"
sha256sum -c SHA256SUMS
python3 -m unittest discover -q -p test_stage37b2d.py >/dev/null
python3 -m py_compile stage37b2d.py
for f in *.sh; do bash -n "$f"; done
install -d -m 700 -- "$INSTALL"
for f in stage37b2d.py README.md SHA256SUMS; do install -m 600 -- "$f" "$INSTALL/$f"; done
for f in 431_commit_when_multitool_closed.sh 432_verify_after_restart.sh 433_rollback_when_multitool_closed.sh 434_status_readonly.sh; do
    install -m 700 -- "$f" "$INSTALL/$f"
done
python3 -IB "$INSTALL/stage37b2d.py" prepare
printf '%s\n' STAGE37B2D_OFFLINE_UNIT_TESTS_PASS STAGE37B2D_INSTALLED_PREPARED_NO_LIVE_CONFIG_CHANGE

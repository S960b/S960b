#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$(pwd -P)" == /home/kali/hermes-pg-stage33ib-ackfix-handoff ]] || exit 2
echo '=== SYSTEMD HANDOFF STATE ==='
systemctl --user show hermes-pg-stage33ib-handoff.service -p LoadState -p ActiveState -p SubState -p Result --no-pager
echo '=== SAFE RECEIPT/PROCESS STATUS ==='
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python handoff33ib.py status

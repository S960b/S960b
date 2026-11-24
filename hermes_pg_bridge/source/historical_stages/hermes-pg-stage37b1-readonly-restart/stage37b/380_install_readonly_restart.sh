#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$EUID" != 0 && "$HOME" == /home/kali ]] || { echo STAGE37B_STOP_WRONG_USER >&2; exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SRC"
sha256sum -c SHA256SUMS
python3 -m unittest discover -q -p test_stage37b.py
for f in ./*.sh; do bash -n "$f"; done
DEST=/home/kali/hermes-pg-stage37b-readonly-restart
[[ ! -e "$DEST" && ! -L "$DEST" ]] || { echo STAGE37B_STOP_ALREADY_INSTALLED_DO_NOT_RETRY >&2; exit 2; }
[[ -f /home/kali/hermes-pg-stage37a-permission-control/stage37a.py ]] || { echo STAGE37B_STOP_STAGE37A_NOT_INSTALLED >&2; exit 2; }
[[ ! -e /home/kali/.config/bridge-pg-v2/managed-mcp-write-permit35c.json && ! -L /home/kali/.config/bridge-pg-v2/managed-mcp-write-permit35c.json ]] || { echo STAGE37B_STOP_PERMIT_PRESENT >&2; exit 2; }
[[ ! -e /home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed && ! -L /home/kali/.config/bridge-pg-v2/managed-writes-stage34d-armed ]] || { echo STAGE37B_STOP_GATE_PRESENT >&2; exit 2; }
systemctl --user is-active --quiet hermes-pg-stage37a-permit-watchdog.timer || { echo STAGE37B_STOP_WATCHDOG_INACTIVE >&2; exit 2; }
mkdir -m 700 "$DEST"
cp -p stage37b.py test_stage37b.py README.md PLAN_37B.md ./*.sh "$DEST/"
chmod 600 "$DEST"/*
python3 -IB "$DEST/stage37b.py" status
echo STAGE37B1_INSTALLED_READONLY_NO_SERVICE_DB_CONFIG_MODIFICATION
echo STAGE37B1_READY_FOR_381_BEFORE_WITH_MULTITOOL_OPEN

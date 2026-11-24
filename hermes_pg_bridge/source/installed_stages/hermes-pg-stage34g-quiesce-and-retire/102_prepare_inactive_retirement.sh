#!/usr/bin/env bash
# No MT stops, no SDK, no config swap; installs one STATIC INACTIVE systemd unit.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
command -v systemctl >/dev/null
[ -x /home/kali/bridge-pg-v2/.venv/bin/python ]
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py prepare-retirement
systemctl --user show hermes-pg-stage34g-retire.service -p ActiveState -p SubState --no-pager
echo STAGE34G_PHASE_B_INACTIVE_HANDOFF_READY

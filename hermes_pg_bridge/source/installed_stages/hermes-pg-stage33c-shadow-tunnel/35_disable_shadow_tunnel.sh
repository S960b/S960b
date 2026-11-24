#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali ]] || exit 2
UNIT="$HOME/.config/systemd/user/bridge-pg-v2-ssh-shadow.service"
TARGET="$HOME/.config/bridge-pg-v2/ssh-shadow-target"
systemctl --user disable --now bridge-pg-v2-ssh-shadow.service >/dev/null
rm -f -- "$UNIT" "$TARGET"
systemctl --user daemon-reload
echo STAGE33C_SHADOW_FORWARD_REMOVED_EXISTING_15432_UNCHANGED

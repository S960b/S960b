#!/usr/bin/env bash
# MANUAL rollback of *only* new loopback server; no config changes anywhere.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || exit 2
UNIT="$HOME/.config/systemd/user/hermes-pg-v2-stage33fb-cg.service"
systemctl --user stop hermes-pg-v2-stage33fb-cg.service || true
if [[ -f "$UNIT" && ! -L "$UNIT" ]]; then rm -f -- "$UNIT";fi
systemctl --user daemon-reload
echo STAGE33FB_LOOPBACK_MCP_DISABLED_LEGACY_UNTOUCHED

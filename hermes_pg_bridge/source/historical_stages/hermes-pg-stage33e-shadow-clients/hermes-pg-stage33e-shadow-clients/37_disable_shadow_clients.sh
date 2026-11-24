#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
UNIT="$HOME/.config/systemd/user/hermes-pg-v2-shadow-cg.service"
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || exit 2
[[ -f "$UNIT" ]] || { echo STAGE33E_STOP_SHADOW_UNIT_NOT_FOUND >&2; exit 2; }
grep -q 'ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage33e-shadow-clients/chatgpt_shadow_mcp.py' "$UNIT" || { echo STAGE33E_STOP_UNKNOWN_UNIT >&2; exit 2; }
systemctl --user stop hermes-pg-v2-shadow-cg.service
rm -- "$UNIT"
systemctl --user daemon-reload
# Keep role adapter candidate for forensic review; never edit opencode.json.
echo STAGE33E_SHADOW_CG_SERVICE_DISABLED_NO_OTHER_CHANGES

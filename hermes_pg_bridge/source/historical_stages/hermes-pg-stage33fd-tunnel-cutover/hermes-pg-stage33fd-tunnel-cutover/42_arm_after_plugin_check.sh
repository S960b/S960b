#!/usr/bin/env bash
# ONLY after ChatGPT independently checks new plugin's two read-only tools.
# Does NOT itself send any message or invoke SDK.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fd-tunnel-cutover
ARM=/home/kali/hermes-pg-stage33fb-mcp-ready/armed-stage33fc
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33FD_STOP_BAD_CONTEXT;exit 2; }
[[ -f switched.json && ! -e "$ARM" && ! -L "$ARM" ]] || { echo STAGE33FD_STOP_ALREADY_ARMED_OR_UNSWITCHED;exit 2; }
# User supplies confirmation only after new role-bound MCP is tested via ordinary ChatGPT chat.
[[ "${STAGE33FD_CHATGPT_PLUGIN_VERIFIED:-}" == 'YES_ROLE_BOUND_18773' ]] || { echo STAGE33FD_STOP_PLUGIN_VALIDATION_REQUIRED;exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33fd.py switched
( set -o noclobber; printf 'ALLOW_FIXED_SEQ5_AFTER_EXCLUSIVE_SDK_SWITCH\n' > "$ARM" )
chmod 600 "$ARM"
[[ "$(stat -c %a "$ARM")" == 600 ]] || exit 2
echo STAGE33FD_FIXED_SEQ5_ARMED_BUT_NOT_SENT
 echo STAGE33FD_CHATGPT_MAY_NOW_CALL_STAGE33F_SEND_ONCE

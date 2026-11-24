#!/usr/bin/env bash
# Start systemd handoff ONLY after owner explicitly agrees to close all MultiTool windows.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34fb-register-pilot
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
[[ -f "$ROOT/previous-opencode.json" && -f "$ROOT/proposed-opencode.json" ]] || exit 2
[[ ! -e "$ROOT/handoff-result.json" ]] || { echo STAGE34FB_STOP_HANDOFF_ALREADY_ATTEMPTED; exit 2; }
[[ "$(systemctl --user is-enabled hermes-pg-stage34fb-handoff.service 2>/dev/null || true)" != enabled ]] || exit 2
for UNIT in bridge-pg-v2-ssh-shadow.service hermes-pg-v2-stage33fb-cg.service hermes-pg-tunnel-stage33fd.service bridge-pg-v2-taskboard.service; do
  systemctl --user is-active --quiet "$UNIT" || { echo STAGE34FB_STOP_DEPENDENCY_INACTIVE; exit 2; }
done
systemctl --user start --no-block hermes-pg-stage34fb-handoff.service
[[ "$(systemctl --user show -p ActiveState --value hermes-pg-stage34fb-handoff.service)" != failed ]] || exit 2
echo STAGE34FB_HANDOFF_ARMED_WAIT_FOR_USER_TO_CLOSE_ALL_MULTITOOL_WINDOWS
echo STAGE34FB_DO_NOT_REOPEN_MULTITOOL_UNTIL_VERIFIED

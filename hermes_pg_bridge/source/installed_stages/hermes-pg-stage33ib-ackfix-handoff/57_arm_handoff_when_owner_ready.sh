#!/usr/bin/env bash
# Only after owner agrees to close ALL MultiTool windows; does not kill processes.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33ib-ackfix-handoff
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
[[ -f "$ROOT/previous-opencode.json" && -f "$ROOT/proposed-opencode.json" ]] || exit 2
[[ ! -e "$ROOT/handoff-result.json" ]] || { echo STAGE33IB_STOP_RESULT_EXISTS; exit 2; }
for UNIT in bridge-pg-v2-ssh-shadow.service hermes-pg-tunnel-stage33fd.service hermes-pg-v2-stage33fb-cg.service; do
  systemctl --user is-active --quiet "$UNIT" || { echo STAGE33IB_STOP_DEPENDENCY_INACTIVE; exit 2; }
done
# User-approved local systemd worker will proceed ONLY when ALL MT processes have exited.
systemctl --user start --no-block hermes-pg-stage33ib-handoff.service
[[ "$(systemctl --user show -p ActiveState --value hermes-pg-stage33ib-handoff.service)" != failed ]] || exit 2
echo STAGE33IB_HANDOFF_ARMED_WAIT_FOR_USER_TO_CLOSE_ALL_MULTITOOL_WINDOWS
echo STAGE33IB_DO_NOT_REOPEN_MULTITOOL_UNTIL_VERIFY

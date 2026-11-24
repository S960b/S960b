#!/usr/bin/env bash
# User-authorized retirement of two Git services + arm one-shot wait.
# Never kills or restarts MultiTool. User must close ALL MT windows manually.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fc-handoff
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
[[ ! -e "$ROOT/handoff-result.json" ]] || { echo STAGE33FC_STOP_RESULT_EXISTS;exit 2; }
[[ -f "$ROOT/original-private.json" && -f "$ROOT/pg-only-cutover.json" ]] || exit 2
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-v2-stage33fb-cg.service)" == active ]] || exit 2
# Previous explicit owner permission: permanently retire Git transport services.
for UNIT in hermes-bridge.service git-dialog-taskboard.service; do
    LOAD=$(systemctl --user show -p LoadState --value "$UNIT" 2>/dev/null || true)
    if [[ "$LOAD" == loaded ]]; then
        systemctl --user disable --now "$UNIT"
        ! systemctl --user is-active --quiet "$UNIT" || { echo STAGE33FC_STOP_GIT_SERVICE_ACTIVE;exit 2; }
    fi
done
echo STAGE33FC_LEGACY_GIT_SERVICES_DISABLED
# Asynchronous *local systemd unit*, not a ChatGPT background task.
systemctl --user start --no-block hermes-pg-stage33fc-handoff.service
[[ "$(systemctl --user show -p ActiveState --value hermes-pg-stage33fc-handoff.service)" != failed ]] || exit 2
echo STAGE33FC_HANDOFF_ARMED_WAITING_FOR_USER_TO_CLOSE_ALL_MULTITOOL_WINDOWS
echo STAGE33FC_DO_NOT_REOPEN_MULTITOOL_UNTIL_COMMIT_VERIFIED

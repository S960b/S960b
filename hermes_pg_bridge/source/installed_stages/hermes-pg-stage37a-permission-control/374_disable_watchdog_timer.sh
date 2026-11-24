#!/usr/bin/env bash
# Emergency watchdog rollout rollback only; NEVER restores previously revoked permits.
set -Eeuo pipefail
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE37A_STOP_WRONG_OWNER >&2; exit 2; }
systemctl --user disable --now hermes-pg-stage37a-permit-watchdog.timer
systemctl --user is-active --quiet hermes-pg-stage37a-permit-watchdog.timer && { echo STAGE37A_STOP_TIMER_STILL_ACTIVE >&2; exit 2; }
echo STAGE37A_WATCHDOG_DISABLED_PERMIT_NOT_REARMED

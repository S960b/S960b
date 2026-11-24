#!/usr/bin/env bash
# Exact pre-arm reversal: never run once seq5 has been committed.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fd-tunnel-cutover
NEW=hermes-pg-tunnel-stage33fd.service
OLD=hermes-pg-tunnel-one-shot.service
ARM=/home/kali/hermes-pg-stage33fb-mcp-ready/armed-stage33fc
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
[[ -f switched.json && ! -e "$ARM" && ! -L "$ARM" ]] || { echo STAGE33FD_STOP_ROLLBACK_BLOCKED_ARMED;exit 2; }
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33fd.py switched
systemctl --user stop "$NEW"
systemctl --user start "$OLD"
[[ "$(systemctl --user is-active "$OLD")" == active ]] || exit 2
[[ "$(systemctl --user is-active "$NEW" || true)" != active ]] || exit 2
rm -f -- switched.json
echo STAGE33FD_ROLLBACK_TO_OLD_ONE_SHOT_COMPLETE_NO_DB_WRITES

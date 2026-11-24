#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKUP=${1:?Usage: 401_rollback.sh /home/kali/.local/state/bridge-pg-v2/stage40/backup-...}
[ "$(id -un)" = kali ] || { echo STAGE40_STOP_WRONG_USER >&2; exit 2; }
python3 -I "$DIR/stage40_patch.py" restore "$BACKUP"
systemctl --user restart bridge-pg-v2-taskboard.service
systemctl --user is-active --quiet bridge-pg-v2-taskboard.service
printf 'STAGE40_ROLLBACK_COMPLETE\n'

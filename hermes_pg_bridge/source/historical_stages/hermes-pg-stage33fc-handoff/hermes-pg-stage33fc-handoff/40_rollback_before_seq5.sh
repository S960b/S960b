#!/usr/bin/env bash
# Manual rollback ONLY with all MultiTool processes shut and no seq5.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$(pwd -P)" == /home/kali/hermes-pg-stage33fc-handoff ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python cutover33fc.py rollback-before-seq5

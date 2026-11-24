#!/usr/bin/env bash
# Only with explicit owner approval after every MT process exits. No SDK/PG modifications.
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$(pwd -P)" == /home/kali/hermes-pg-stage33ib-ackfix-handoff ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python handoff33ib.py rollback

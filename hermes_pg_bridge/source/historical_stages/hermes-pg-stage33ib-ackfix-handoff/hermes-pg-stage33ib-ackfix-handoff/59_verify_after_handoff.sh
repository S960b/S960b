#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$(pwd -P)" == /home/kali/hermes-pg-stage33ib-ackfix-handoff ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python handoff33ib.py verify
echo STAGE33IB_VERIFY_SUCCESS_POST_CONFIG_SWAP

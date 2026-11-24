#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && "$EUID" -ne 0 ]] || exit 2
cd /home/kali/hermes-pg-stage35s-connector-observer
python3 stage35s.py restore

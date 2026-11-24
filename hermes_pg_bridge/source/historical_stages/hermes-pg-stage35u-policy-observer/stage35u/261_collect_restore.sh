#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && "$EUID" -ne 0 ]] || exit 2
cd /home/kali/hermes-pg-stage35u-connector-observer
python3 stage35u.py collect

#!/usr/bin/env bash
# Stage35R: after precisely one ChatGPT plugin attempt, restore original route.
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && $EUID -ne 0 ]] || exit 2
cd /home/kali/hermes-pg-stage35r-connector-observer
python3 stage35r.py collect

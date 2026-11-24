#!/usr/bin/env bash
# Stage35R owner-initiated immediate restore (also a systemd timer restores at 12m).
set -Eeuo pipefail
set +x
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && $EUID -ne 0 ]] || exit 2
cd /home/kali/hermes-pg-stage35r-connector-observer
python3 stage35r.py restore

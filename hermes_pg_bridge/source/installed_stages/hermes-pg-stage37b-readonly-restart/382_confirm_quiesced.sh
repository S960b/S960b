#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$EUID" != 0 && "$HOME" == /home/kali ]] || { echo STAGE37B_STOP_WRONG_USER >&2; exit 2; }
exec python3 -IB /home/kali/hermes-pg-stage37b-readonly-restart/stage37b.py quiet

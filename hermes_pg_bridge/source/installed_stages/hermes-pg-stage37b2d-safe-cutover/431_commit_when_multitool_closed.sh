#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE37B2D_STOP_USER;exit 2; }
python3 -IB /home/kali/hermes-pg-stage37b2d-safe-cutover/stage37b2d.py commit

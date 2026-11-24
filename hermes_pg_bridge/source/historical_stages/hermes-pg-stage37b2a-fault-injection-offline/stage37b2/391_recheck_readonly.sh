#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$EUID" != 0 && "$HOME" == /home/kali ]] || { echo STAGE37B2_STOP_WRONG_USER >&2; exit 2; }
python3 -IB /home/kali/hermes-pg-stage37b2-fault-injection/stage37b2.py status
node /home/kali/hermes-pg-stage37b2-fault-injection/fault_reconciliation37b2.cjs

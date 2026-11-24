#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE36B_STOP_WRONG_USER; exit 2; }
cd /home/kali/hermes-pg-stage36b-managed-inbox-handoff
python3 -IB stage36b.py commit

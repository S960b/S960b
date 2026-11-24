#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $(id -un) == kali && "$HOME" == /home/kali && $# == 1 ]] || { echo STAGE36_STOP_WRONG_USER_OR_ARGS; exit 2; }
/home/kali/bridge-pg-v2/.venv/bin/python -IB /home/kali/hermes-pg-stage36-controlled-e2e/stage36.py scope "$1"

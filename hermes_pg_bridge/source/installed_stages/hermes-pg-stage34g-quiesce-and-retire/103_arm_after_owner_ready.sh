#!/usr/bin/env bash
# Starts a waiting one-shot, requires explicit owner direction to close all MT windows.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage34g-quiesce-and-retire
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage34g.py arm

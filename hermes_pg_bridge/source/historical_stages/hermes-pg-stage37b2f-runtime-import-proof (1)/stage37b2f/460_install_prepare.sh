#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE37B2F_STOP_USER; exit 2; }
ROOT=/home/kali/hermes-pg-stage37b2f-runtime-import-proof
SRC="$(cd -- "$(dirname "$0")" && pwd -P)"
[[ ! -e "$ROOT" ]] || { echo STAGE37B2F_STOP_ALREADY_INSTALLED; exit 2; }
cd "$SRC"
sha256sum -c SHA256SUMS
python3 -m unittest -q test_stage37b2f.py
mkdir -m 700 -- "$ROOT"
install -m 600 stage37b2f.py "$ROOT/stage37b2f.py"
python3 -IB "$ROOT/stage37b2f.py" prepare

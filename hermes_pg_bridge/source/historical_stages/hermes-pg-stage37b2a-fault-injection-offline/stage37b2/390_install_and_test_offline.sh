#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$EUID" != 0 && "$HOME" == /home/kali ]] || { echo STAGE37B2_STOP_WRONG_USER >&2; exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SRC"
sha256sum -c SHA256SUMS
python3 -m unittest discover -q -p test_stage37b2.py
for f in ./*.sh; do bash -n "$f"; done
node --check fault_reconciliation37b2.cjs
python3 -IB stage37b2.py preflight
DEST=/home/kali/hermes-pg-stage37b2-fault-injection
[[ ! -e "$DEST" && ! -L "$DEST" ]] || { echo STAGE37B2_STOP_ALREADY_INSTALLED_USE_391 >&2; exit 2; }
mkdir -m 700 "$DEST"
cp stage37b2.py test_stage37b2.py fault_reconciliation37b2.cjs README.md ./*.sh "$DEST/"
chmod 600 "$DEST"/*
node "$DEST/fault_reconciliation37b2.cjs"
python3 -IB "$DEST/stage37b2.py" status
echo STAGE37B2_FAULT_INJECTION_OFFLINE_PASS_NO_DB_WRITE_NO_SERVICE_CHANGE

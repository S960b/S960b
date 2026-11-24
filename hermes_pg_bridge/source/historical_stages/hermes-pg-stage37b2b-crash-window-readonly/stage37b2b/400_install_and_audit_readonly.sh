#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$EUID" != 0 && "$HOME" == /home/kali ]] || { echo STAGE37B2B_STOP_WRONG_USER >&2; exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SRC"
sha256sum -c SHA256SUMS
python3 -m unittest discover -q -p 'test_audit37b2b.py'
node --check crash_intent_model37b2b.cjs
for f in ./*.sh; do bash -n "$f"; done
# Before installation, verify the live environment without changing it.
python3 -IB /home/kali/hermes-pg-stage37b2-fault-injection/stage37b2.py preflight
DEST=/home/kali/hermes-pg-stage37b2b-crash-intent-audit
[[ ! -e "$DEST" && ! -L "$DEST" ]] || { echo STAGE37B2B_STOP_ALREADY_INSTALLED >&2; exit 2; }
mkdir -m 700 "$DEST"
cp audit37b2b.py crash_intent_model37b2b.cjs test_audit37b2b.py 400_install_and_audit_readonly.sh 401_recheck_readonly.sh README.md "$DEST/"
chmod 600 "$DEST"/*
python3 -IB "$DEST/audit37b2b.py" audit
node "$DEST/crash_intent_model37b2b.cjs"
echo STAGE37B2B_AUDIT_COMPLETE_READONLY_NO_LIVE_CRASH

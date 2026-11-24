#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
TARGET=/home/kali/hermes-pg-stage36b-managed-inbox-handoff
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE36B_STOP_WRONG_USER; exit 2; }
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] || { echo STAGE36B_STOP_ALREADY_INSTALLED; exit 2; }
cd "$SOURCE"
sha256sum -c SHA256SUMS
python3 -m unittest discover -q -p 'test_stage36b.py'
python3 -m py_compile stage36b.py test_stage36b.py
for f in *.sh; do bash -n "$f"; done
mkdir -m 700 "$TARGET"
for f in stage36b.py 311_commit_when_multitool_closed.sh 312_status_readonly.sh 313_emergency_revoke_gate.sh 314_rollback_before_claim.sh 315_progress_readonly.sh README.md; do
  install -m 600 "$SOURCE/$f" "$TARGET/$f"
done
chmod 700 "$TARGET"/*.sh
cd "$TARGET"
python3 -IB stage36b.py prepare
echo STAGE36B_INSTALLED_PREPARED_NO_CONFIG_CHANGE

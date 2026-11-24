#!/usr/bin/env bash
# Stage 37B2C1. No live plugin, config, DB, SDK or permission modifications.
set -Eeuo pipefail
umask 077
export PATH="/home/kali/.hermes/node/bin:$PATH"
unset NODE_OPTIONS NODE_PATH
[[ "$(id -un)" == kali && "$HOME" == /home/kali && $(id -u) -ne 0 ]] || { echo STAGE37B2C1_STOP_UNEXPECTED_USER; exit 2; }
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT=/home/kali/hermes-pg-stage37b2c-claim-intent
INSTALL=/home/kali/hermes-pg-stage37b2c1-revalidate
NODE=/home/kali/.hermes/node/bin/node
[[ -x "$NODE" ]] || { echo STAGE37B2C1_STOP_NODE_UNAVAILABLE; exit 2; }
[[ -d "$ROOT" && ! -L "$ROOT" && ! -e "$INSTALL" && ! -L "$INSTALL" ]] || { echo STAGE37B2C1_STOP_CANDIDATE_OR_INSTALL_STATE; exit 2; }
[[ -f "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs" && -f "$ROOT/render37b2c.py" && -f "$ROOT/test_intent37b2c.mjs" ]] || { echo STAGE37B2C1_STOP_MISSING_OLD_CANDIDATE; exit 2; }
cd "$HERE"
sha256sum -c SHA256SUMS
python3 -m unittest discover -q -p 'test_stage37b2c1.py'
"$NODE" --check test_candidate37b2c1.cjs
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs"
# Strict verification against current pinned source, trusted helper and 8-plugin config;
# rejects active permit/gate or unexpected source/config drift.
python3 -IB "$ROOT/render37b2c.py" verify
"$NODE" "$HERE/test_candidate37b2c1.cjs" "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs"
# Source-only test had omitted ctx.taskIds; it must stay unchanged for auditability.
python3 -IB "$HERE/check_old_harness37b2c1.py"
# Only after successful checks, install new verifier for future repeatable READ-ONLY audits.
mkdir -m 700 -- "$INSTALL"
for f in test_candidate37b2c1.cjs check_old_harness37b2c1.py README.md; do
  install -m 600 "$HERE/$f" "$INSTALL/$f"
done
printf '%s\n' \
  STAGE37B2C1_OLD_SANDBOX_MISSING_TASKIDS_ROOT_CAUSE \
  STAGE37B2C1_EXISTING_CANDIDATE_VERIFIED_WITHOUT_REGENERATION \
  STAGE37B2C1_NO_ACTIVE_PLUGIN_OR_DB_CONFIG_CHANGE \
  STAGE37B2C1_NO_PERMIT_GATE_OR_SDK_ACTION \
  STAGE37B2C1_REVALIDATION_PASS_UNREGISTERED_CANDIDATE_ONLY

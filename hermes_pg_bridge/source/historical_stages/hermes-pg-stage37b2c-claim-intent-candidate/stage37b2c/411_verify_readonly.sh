#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/home/kali/hermes-pg-stage37b2c-claim-intent
NODE=/home/kali/.hermes/node/bin/node
[[ -x "$NODE" ]] || { echo STAGE37B2C_STOP_NODE_UNAVAILABLE; exit 2; }
python3 -IB "$ROOT/render37b2c.py" verify
"$NODE" --check "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs"
"$NODE" "$ROOT/test_candidate37b2c.cjs" "$ROOT/mt-pg-inbox-managed-stage37b2c-unregistered.mjs"
printf '%s\n' STAGE37B2C_READONLY_CANDIDATE_VERIFIED

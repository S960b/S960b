#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33j-ack-recovery
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
# Preflight is entirely read-only; operation below is ONE non-retriable DB test.
bash 61_preflight.sh
echo STAGE33J_START_ONE_ISOLATED_TEST
# The subprocess can commit part of the test before a failure. NEVER retry.
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python stage33j.py run-once
echo STAGE33J_ONE_SHOT_EXECUTION_COMPLETE

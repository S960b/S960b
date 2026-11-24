#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33e-shadow-clients
[[ "$(id -un)" == kali && "$(pwd -P)" == "$ROOT" ]] || exit 2
sha256sum -c SHA256SUMS >/dev/null
[[ "$(systemctl --user is-active hermes-pg-v2-shadow-cg.service)" == active ]] || exit 2
/usr/bin/curl --noproxy '*' --silent --fail --max-time 3 http://127.0.0.1:18772/ping >/dev/null || exit 2
/home/kali/.hermes/node/bin/node --check "$ROOT/mt-pg-inbox-agent-shadow.mjs"
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python verify33e.py
# Verification is intentionally read-only and cannot modify service state.
echo STAGE33E_VERIFY_ONLY_COMPLETE

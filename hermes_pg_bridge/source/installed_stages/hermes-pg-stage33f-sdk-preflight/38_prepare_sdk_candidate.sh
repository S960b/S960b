#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33f-sdk-preflight
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33F_STOP_WRONG_CONTEXT >&2;exit 2; }
[[ ! -e mt-pg-inbox-agent-stage33f.mjs && ! -L mt-pg-inbox-agent-stage33f.mjs ]] || { echo STAGE33F_STOP_ALREADY_PREPARED >&2; exit 2; }
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE33F_STOP_RUNTIME_NOT_FOUND >&2;exit 2; }
# Prove Stage 33E is healthy before generating a local candidate.
env -u APPDIR -u APPIMAGE "$PY" /home/kali/hermes-pg-stage33e-shadow-clients/verify33e.py
# Generate the candidate; do not add it to MultiTool configuration.
env -u APPDIR -u APPIMAGE "$PY" render_candidate33f.py
"$NODE" --check mt-pg-inbox-agent-stage33f.mjs
echo STAGE33F_NODE_ACTIVE_CAPABLE_CANDIDATE_SYNTAX_OK
# This inventory performs only SELECT checks against the already completed canary.
env -u APPDIR -u APPIMAGE "$PY" inventory33f.py
echo STAGE33F_PREPARE_COMPLETED_NO_SDK_DISPATCH

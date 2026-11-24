#!/usr/bin/env bash
# Stage 33I-B install: prepare exact config and an INACTIVE systemd worker.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33ib-ackfix-handoff
PY=/home/kali/bridge-pg-v2/.venv/bin/python
NODE=/home/kali/.hermes/node/bin/node
UNIT="$HOME/.config/systemd/user/hermes-pg-stage33ib-handoff.service"
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33IB_STOP_BAD_CONTEXT; exit 2; }
[[ -x "$PY" && -x "$NODE" ]] || { echo STAGE33IB_STOP_RUNTIME_MISSING; exit 2; }
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || { echo STAGE33IB_STOP_UNIT_ALREADY_EXISTS; exit 2; }
"$NODE" --check /home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs
"$NODE" /home/kali/hermes-pg-stage33i-ackfix/test_reconcile33i.cjs /home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs
# Existing real actor roles, exact canary receipts and config SHA are rechecked in prepare.
env -u APPDIR -u APPIMAGE "$PY" handoff33ib.py prepare
mkdir -p -m 700 "$HOME/.config/systemd/user"
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=Hermes PG v2 Stage 33I-B one-shot ACK fix config handoff after MultiTool shutdown
Wants=bridge-pg-v2-ssh-shadow.service
After=bridge-pg-v2-ssh-shadow.service

[Service]
Type=oneshot
WorkingDirectory=/home/kali/hermes-pg-stage33ib-ackfix-handoff
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage33ib-ackfix-handoff/handoff33ib.py worker
TimeoutStartSec=1100
RemainAfterExit=yes
Restart=no
NoNewPrivileges=yes
Environment=PYTHONNOUSERSITE=1
UnsetEnvironment=APPDIR APPIMAGE
UMask=0077
UNITFILE
chmod 600 "$UNIT"
systemctl --user daemon-reload
[[ "$(systemctl --user is-enabled hermes-pg-stage33ib-handoff.service 2>/dev/null || true)" != enabled ]] || exit 2
if systemctl --user is-active --quiet hermes-pg-stage33ib-handoff.service; then exit 2; fi
echo STAGE33IB_HANDOFF_UNIT_INSTALLED_INACTIVE

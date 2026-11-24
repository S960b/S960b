#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE33C_STOP_WRONG_USER >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
[[ "$ROOT" == /home/kali/hermes-pg-stage33c-shadow-tunnel ]] || { echo STAGE33C_STOP_UNEXPECTED_INSTALL_PATH >&2; exit 2; }
cd "$ROOT"
sha256sum -c SHA256SUMS >/dev/null
[[ -n "${PG_V2_SSH_TARGET:-}" ]] || { echo STAGE33C_STOP_PRIVATE_SSH_TARGET_MISSING >&2; exit 2; }
[[ -x /home/kali/bridge-pg-v2/.venv/bin/python && -x /usr/bin/ssh ]] || { echo STAGE33C_STOP_DEPENDENCIES_MISSING >&2; exit 2; }
[[ -f /home/kali/.ssh/mt_tun_ed25519 && -f /home/kali/.ssh/known_hosts ]] || { echo STAGE33C_STOP_SSH_IDENTITY_MISSING >&2; exit 2; }
UNIT="$HOME/.config/systemd/user/bridge-pg-v2-ssh-shadow.service"
TARGET="$HOME/.config/bridge-pg-v2/ssh-shadow-target"
[[ ! -e "$UNIT" && ! -e "$TARGET" ]] || { echo STAGE33C_STOP_ALREADY_INSTALLED >&2; exit 2; }
# Never disturb existing 15432/other services; requires free NEW port.
if /usr/bin/python3 - <<'PY'
import socket
s=socket.socket()
try:
 s.bind(('127.0.0.1',15433))
except OSError:
 raise SystemExit(1)
finally:
 s.close()
PY
then :; else echo STAGE33C_STOP_SHADOW_PORT_IN_USE >&2; exit 2; fi
# Stage 33C must NOT depend on the unreliable legacy 15432 tunnel.
# Verify private login files exist; real DB login checks are done on new 15433.
[[ -d /home/kali/.config/bridge-pg-v2/actor-credentials ]] || { echo STAGE33C_STOP_ROLE_CREDENTIALS_MISSING >&2; exit 2; }
[[ -f /home/kali/.config/bridge-pg-v2/actor-credentials/bridge_chatgpt.pgpass && -f /home/kali/.config/bridge-pg-v2/actor-credentials/bridge_agent.pgpass ]] || { echo STAGE33C_STOP_ROLE_CREDENTIALS_MISSING >&2; exit 2; }
mkdir -p "$HOME/.config/systemd/user"
install_rollback(){
 rc=$?
 if [[ $rc -ne 0 ]]; then
  systemctl --user disable --now bridge-pg-v2-ssh-shadow.service >/dev/null 2>&1 || :
  rm -f -- "$UNIT" "$TARGET"
  systemctl --user daemon-reload >/dev/null 2>&1 || :
  echo STAGE33C_FAILED_SHADOW_SERVICE_ROLLED_BACK >&2
 fi
 return "$rc"
}
trap install_rollback EXIT
/usr/bin/python3 "$ROOT/ssh_shadow_runner.py" configure
cat > "$UNIT" <<'UNIT'
[Unit]
Description=Hermes PG v2 SSH shadow forward to PostgreSQL (local port 15433)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/kali/hermes-pg-stage33c-shadow-tunnel/ssh_shadow_runner.py run
Restart=always
RestartSec=5s
NoNewPrivileges=true
[Install]
WantedBy=default.target
UNIT
chmod 600 "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now bridge-pg-v2-ssh-shadow.service >/dev/null
for _ in {1..20}; do
 if /usr/bin/python3 - <<'PY'
import socket
try:
 s=socket.create_connection(('127.0.0.1',15433),timeout=0.5);s.close()
except OSError:raise SystemExit(1)
PY
 then break; fi
 sleep 1
done
bash "$ROOT/35_verify_shadow_tunnel.sh"
trap - EXIT
echo STAGE33C_PERSISTENT_SHADOW_SSH_UNIT_ACTIVE
echo STAGE33C_LEGACY_15432_AND_ALL_OTHER_SERVICES_UNCHANGED

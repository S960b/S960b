#!/usr/bin/env bash
# Set up a disabled one-shot handoff service. No MT or DB changes.
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33fc-handoff
PY=/home/kali/bridge-pg-v2/.venv/bin/python
UNIT="$HOME/.config/systemd/user/hermes-pg-stage33fc-handoff.service"
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33FC_STOP_BAD_CONTEXT;exit 2; }
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || { echo STAGE33FC_STOP_UNIT_EXISTS;exit 2; }
[[ -x "$PY" ]] || exit 2
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || exit 2
[[ "$(systemctl --user is-active hermes-pg-v2-stage33fb-cg.service)" == active ]] || exit 2
env -u APPDIR -u APPIMAGE "$PY" cutover33fc.py prepare
mkdir -p -m 700 "$HOME/.config/systemd/user"
cat > "$UNIT" <<'UNITFILE'
[Unit]
Description=Hermes PG Stage 33F-C one-shot safe config handoff after manual MultiTool shutdown
Wants=bridge-pg-v2-ssh-shadow.service
After=bridge-pg-v2-ssh-shadow.service

[Service]
Type=oneshot
WorkingDirectory=/home/kali/hermes-pg-stage33fc-handoff
ExecStart=/home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage33fc-handoff/cutover33fc.py watch-commit
TimeoutStartSec=1100
RemainAfterExit=yes
Restart=no
NoNewPrivileges=yes
UMask=0077
UNITFILE
chmod 600 "$UNIT"
systemctl --user daemon-reload
[[ "$(systemctl --user is-enabled hermes-pg-stage33fc-handoff.service 2>/dev/null || true)" != enabled ]] || exit 2
systemctl --user is-active --quiet hermes-pg-stage33fc-handoff.service && exit 2 || true
echo STAGE33FC_HANDOFF_UNIT_INSTALLED_NOT_ARMED

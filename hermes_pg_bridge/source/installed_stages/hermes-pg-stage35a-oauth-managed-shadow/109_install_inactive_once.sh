#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage35a-oauth-managed-shadow
UNIT=/home/kali/.config/systemd/user/hermes-pg-stage35a-oauth-shadow.service
cd "$ROOT"
bash 108_readonly_preflight.sh
mkdir -p -m 0700 /home/kali/.config/systemd/user
[[ ! -e "$UNIT" && ! -L "$UNIT" ]] || exit 2
# Create-only and private. Deliberately DO NOT start or enable.
( set -o noclobber; cat >"$UNIT" <<'EOF'
[Unit]
Description=Hermes PostgreSQL Stage 35A OAuth-protected managed MCP shadow (inactive)
After=bridge-pg-v2-ssh-shadow.service
Wants=bridge-pg-v2-ssh-shadow.service

[Service]
Type=simple
WorkingDirectory=/home/kali/hermes-pg-stage35a-oauth-managed-shadow
ExecStart=/usr/bin/env -u APPDIR -u APPIMAGE PYTHONDONTWRITEBYTECODE=1 /home/kali/bridge-pg-v2/.venv/bin/python /home/kali/hermes-pg-stage35a-oauth-managed-shadow/managed_mcp35a.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
UMask=0077

[Install]
# intentionally never enabled in Stage35A
EOF
)
chmod 600 "$UNIT"
systemctl --user daemon-reload
[[ "$(systemctl --user is-active hermes-pg-stage35a-oauth-shadow.service 2>/dev/null || true)" != active ]] || exit 2
[[ "$(systemctl --user is-enabled hermes-pg-stage35a-oauth-shadow.service 2>/dev/null || true)" != enabled ]] || exit 2
echo STAGE35A_OAUTH_SHADOW_SYSTEMD_UNIT_CREATED_INACTIVE
bash 110_verify_only.sh

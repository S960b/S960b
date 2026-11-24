#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
ROOT=/home/kali/hermes-pg-stage33e-shadow-clients
[[ "$(id -un)" == kali && "$HOME" == /home/kali && "$(pwd -P)" == "$ROOT" ]] || { echo STAGE33E_STOP_WRONG_CONTEXT >&2; exit 2; }
sha256sum -c SHA256SUMS >/dev/null
[[ "$(systemctl --user is-active bridge-pg-v2-ssh-shadow.service)" == active ]] || { echo STAGE33E_STOP_SHADOW_FORWARD_DOWN >&2; exit 2; }
/usr/bin/pg_isready -h 127.0.0.1 -p 15433 -q || { echo STAGE33E_STOP_SHADOW_POSTGRES_DOWN >&2; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-tunnel-one-shot.service)" == active ]] || { echo STAGE33E_STOP_EXISTING_SECURE_TUNNEL_NOT_ACTIVE >&2; exit 2; }
[[ "$(systemctl --user is-active hermes-pg-one-shot-canary.service)" == active ]] || { echo STAGE33E_STOP_EXISTING_WRITE_CANARY_NOT_ACTIVE >&2; exit 2; }
[[ ! -e "$ROOT/mt-pg-inbox-agent-shadow.mjs" ]] || { echo STAGE33E_STOP_SHADOW_CANDIDATE_ALREADY_EXISTS >&2; exit 2; }
[[ ! -e "$HOME/.config/systemd/user/hermes-pg-v2-shadow-cg.service" ]] || { echo STAGE33E_STOP_SHADOW_UNIT_ALREADY_EXISTS >&2; exit 2; }
# Verify actor client without executing SQL writes or MultiTool SDK.
env -u APPDIR -u APPIMAGE /home/kali/bridge-pg-v2/.venv/bin/python - <<'PY'
from actor_import33e import actor_classes
ChatGPT, Agent=actor_classes()
for cls in (ChatGPT,Agent):
    assert cls().health()['ok'] is True
print('STAGE33E_REAL_ACTOR_LOGINS_PREFLIGHT_OK')
PY
echo STAGE33E_SHADOW_READ_ONLY_PREFLIGHT_OK

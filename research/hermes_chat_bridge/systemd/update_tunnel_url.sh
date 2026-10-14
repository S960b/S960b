#!/usr/bin/env bash
# Sync the current trycloudflare URL everywhere the bridge needs it.
# Runs every 60s via hermes-tunnel-url.timer.
#
# On each run:
#   1) write the current tunnel URL into current_tunnel_url.txt (taskboard)
#   2) if BRIDGE_BASE in pilot_environment.private.json differs from the
#      current URL -> update it (atomic JSON rewrite, secrets preserved) and
#      restart hermes-bridge.service so OAuth discovery advertises the live
#      tunnel URL. Retries on the next tick if the update fails.
#
# Why a timer and not ExecStartPost: the tunnel log is append-only and mixes
# URLs from every restart; at ExecStartPost time the new URL is NOT yet in the
# log, so a post-start hook grabs the PREVIOUS run's URL. A 60s timer reads
# tail -1 when the current cloudflared has definitely printed its URL.
#
# If the tunnel service is dead/restarting, tail -1 is the last known URL -
# we keep whatever is already in the file (do not overwrite with older).
set -u

LOG=/home/kali/.local/state/hermes-bridge/cloudflared_pilot.log
OUT=/home/kali/.local/state/hermes-bridge/current_tunnel_url.txt
ENV_FILE=/home/kali/.local/state/hermes-bridge/pilot-20261004T140717Z/pilot_environment.private.json
SYNC_LOG=/home/kali/.local/state/hermes-bridge/tunnel_url_sync.log
PY=/home/kali/.venvs/bridge-clean/bin/python

log() { echo "$(date -Is) $*" >> "$SYNC_LOG"; }

# only touch anything when the tunnel unit is active
if ! systemctl --user is-active --quiet hermes-tunnel.service 2>/dev/null; then
  exit 0
fi

url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1)
if [ -z "$url" ]; then
  exit 0
fi

# 1) taskboard file
old=""
[ -f "$OUT" ] && old=$(cat "$OUT")
if [ "$url" != "$old" ]; then
  printf '%s\n' "$url" > "$OUT"
  log "tunnel url -> $url (file updated)"
fi

# 2) BRIDGE_BASE sync + bridge restart (only when it actually changed)
#    python exits: 0 = updated, 2 = already current, 1 = error
"$PY" - "$ENV_FILE" "$url" <<'PY'
import json, os, sys, tempfile
env_path, new_base = sys.argv[1], sys.argv[2]
with open(env_path, encoding='utf-8') as f:
    data = json.load(f)
if data.get('BRIDGE_BASE') == new_base:
    sys.exit(2)
data['BRIDGE_BASE'] = new_base
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(env_path), prefix='.env-', suffix='.tmp')
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, env_path)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
print(f'BRIDGE_BASE: {data.get("BRIDGE_BASE")} -> {new_base}')
sys.exit(0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  log "BRIDGE_BASE changed -> restarting hermes-bridge"
  systemctl --user restart hermes-bridge.service
  sleep 3
  if curl -s -m 5 -o /dev/null http://127.0.0.1:8765/ping; then
    log "bridge restarted, ping OK"
  else
    log "WARN: bridge restarted but ping FAILED"
  fi
elif [ "$rc" -eq 2 ]; then
  : # already current, nothing to do
elif [ "$rc" -eq 127 ]; then
  log "WARN: python missing at $PY"
else
  log "WARN: BRIDGE_BASE update failed (rc=$rc), will retry next tick"
fi
exit 0
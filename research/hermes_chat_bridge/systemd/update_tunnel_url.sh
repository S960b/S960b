#!/usr/bin/env bash
# Sync the current trycloudflare URL into current_tunnel_url.txt (consumed by
# the taskboard). Runs every 60s via hermes-tunnel-url.timer.
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

# only touch the file when the tunnel unit is active
if ! systemctl --user is-active --quiet hermes-tunnel.service 2>/dev/null; then
  exit 0
fi

url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1)
if [ -z "$url" ]; then
  exit 0
fi

old=""
[ -f "$OUT" ] && old=$(cat "$OUT")
if [ "$url" != "$old" ]; then
  printf '%s\n' "$url" > "$OUT"
  echo "$(date -Is) tunnel url -> $url" >> /home/kali/.local/state/hermes-bridge/tunnel_url_sync.log
fi
exit 0
#!/usr/bin/env bash
# LinkedIn daily post cycle - resilient wrapper (cardinal fix / 2026-10-14)
#
# Diagnosis behind this:
#   On 2026-10-13 20:00 MSK the job FIRED and ran (output file created) but died
#   on an upstream HTTP 502 "prefill leg failed" from the DeepSeek router
#   (https://gw2.gigatool.app/deepseek). The single 20:00 tick did ~3 fast retries
#   in ~9s, ALL against a momentarily dead upstream, so the day silently had no
#   post. Pinning was NOT the problem - the job is pinned to
#   deepseek-v4-flash / custom:multitool-deepseek and that matched the active model.
#   So "make the job adopt the active model" would NOT have helped here.
#
# Real fix: re-trigger the job with backoff across the evening window
# (host is on 16:00-22:00 MSK). A later attempt succeeds once the router is back.
#
# Idempotent: stops as soon as today's post is covered (published OR queued),
# so it can never double-post. Re-running is safe because the LinkedIn skill
# skips when today is already in PUBLISHED.md / QUEUE/.
set -u

JOB=9fc7abfe87a3
HERMES=/home/kali/.local/bin/hermes
LINK=/home/kali/linkedin
LOG=/home/kali/.hermes/cron/os_linkedin_resilient.log
TODAY=$(date +%Y-%m-%d)

post_done() {
  grep -q -- "$TODAY" "$LINK/PUBLISHED.md" 2>/dev/null && return 0
  [ -f "$LINK/QUEUE/$TODAY.md" ] && return 0
  return 1
}

tick_once() {
  "$HERMES" cron run "$JOB" --accept-hooks >>"$LOG" 2>&1
  "$HERMES" cron tick --accept-hooks >>"$LOG" 2>&1
}

echo "==== [$(date '+%F %T %Z')] LinkedIn resilient wrapper start (TODAY=$TODAY) ====" >>"$LOG"

if post_done; then
  echo "[$(date '+%F %T')] Today already covered - nothing to do." >>"$LOG"
  exit 0
fi

# backoff seconds between attempts: immediate, +4m, +10m, +25m (all inside 16-22 window)
backoff=(0 240 600 1500)
for i in "${!backoff[@]}"; do
  if [ "$i" -gt 0 ]; then
    echo "[$(date '+%F %T')] sleeping ${backoff[$i]}s before attempt $((i+1))..." >>"$LOG"
    sleep "${backoff[$i]}"
  fi
  echo "[$(date '+%F %T')] --- attempt $((i+1))/${#backoff[@]} ---" >>"$LOG"
  tick_once
  if post_done; then
    echo "[$(date '+%F %T')] SUCCESS after attempt $((i+1))." >>"$LOG"
    exit 0
  fi
  echo "[$(date '+%F %T')] attempt $((i+1)) did not cover today yet." >>"$LOG"
done

echo "[$(date '+%F %T')] GAVE UP after ${#backoff[@]} attempts. Post for $TODAY not produced." >>"$LOG"
exit 0
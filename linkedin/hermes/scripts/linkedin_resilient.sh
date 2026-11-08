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

# v2 (2026-10-16): "covered" means PUBLISHED, NOT queued.
# Case that forced this: 2026-10-16 the cron cycle hit its iteration budget right
# after writing QUEUE/2026-10-16.md and never reached the API publish step, while
# v1 post_done() treated the QUEUE file as "today covered" for the next attempts.
post_published() {
  grep -q -- "$TODAY" "$LINK/PUBLISHED.md" 2>/dev/null
}

# Publish a ready post directly from QUEUE via the official API (one attempt).
# Returns 0 only when the API returned RESULT=PUBLISHED and the post was logged.
publish_queued() {
  local q="$LINK/QUEUE/$TODAY.md" out urn
  [ -f "$q" ] || return 1
  out=$(python3 "$LINK/publish_queued.py" "$q" 2>&1)
  echo "$out" >>"$LOG"
  case "$out" in
    *RESULT=PUBLISHED*)
      urn=$(echo "$out" | sed -n 's/^URN=//p')
      mv "$q" "$LINK/PUBLISHED/$TODAY.md"
      printf '\n## %s (auto-published from QUEUE by resilient wrapper v2)\n\nDATE: %s\nLINKEDIN POST ID/URL: %s\n' \
        "$TODAY" "$TODAY" "${urn:-unknown}" >>"$LINK/PUBLISHED.md"
      python3 "$LINK/tg_report.py" "LinkedIn $TODAY: auto-published queued post - https://www.linkedin.com/feed/update/${urn:-}/" >>"$LOG" 2>&1 || true
      return 0
      ;;
  esac
  return 1
}

tick_once() {
  "$HERMES" cron run "$JOB" --accept-hooks >>"$LOG" 2>&1
  "$HERMES" cron tick --accept-hooks >>"$LOG" 2>&1
}

echo "==== [$(date '+%F %T %Z')] LinkedIn resilient wrapper start (TODAY=$TODAY) ====" >>"$LOG"

if post_published; then
  echo "[$(date '+%F %T')] Today already published - nothing to do." >>"$LOG"
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

  # 1) A queued post is NOT published yet - publish it directly.
  #    Run the full cron cycle only when there is nothing queued (avoids burning
  #    the agent's iteration budget on a cycle that would just re-queue).
  if [ -f "$LINK/QUEUE/$TODAY.md" ]; then
    if publish_queued; then
      echo "[$(date '+%F %T')] SUCCESS after attempt $((i+1)) (published from QUEUE)." >>"$LOG"
      exit 0
    fi
    echo "[$(date '+%F %T')] publish_queued failed on attempt $((i+1)); retrying later." >>"$LOG"
    continue
  fi

  tick_once
  if post_published; then
    echo "[$(date '+%F %T')] SUCCESS after attempt $((i+1))." >>"$LOG"
    exit 0
  fi
  echo "[$(date '+%F %T')] attempt $((i+1)) did not cover today yet." >>"$LOG"
done

echo "[$(date '+%F %T')] GAVE UP after ${#backoff[@]} attempts. Post for $TODAY not produced." >>"$LOG"
exit 0
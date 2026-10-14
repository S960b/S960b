# LinkedIn account restricted - 2026-09-27

## STATUS: RESOLVED 2026-09-28 (access restored)

Publishing resumed via the official API and works: posts on 2026-09-28, 2026-09-29 and 2026-09-30
were all created (HTTP 201) and each Telegram report was sent. The daily cron job is enabled and
scheduled again (job 9fc7abfe87a3, pinned to provider=custom / model=deepseek-v4-flash-0731).
Keep this file as the incident record; the re-entry checklist below still applies after any future
restriction.

## What happened

The account now shows:

> Access to your account has been temporarily restricted. To restore access, send us a copy of a
> government-issued ID document.

The 4th post of this cycle (SunshineCTF 2026 recap, published 2026-09-27 ~21:05 MSK, HTTP 201,
urn:li:share:7510034937832534017) was created on the API side; the restriction appeared right
after that window.

## What the publishing history looked like

| date | action | channel |
|------|--------|---------|
| 2026-09-24 | OAuth authorisation + first post published | official Posts API (curl/urllib) |
| 2026-09-25 | post published | official Posts API |
| 2026-09-26 | post published | official Posts API |
| 2026-09-27 | post published with image | official Posts API (images + posts) |

## Likely triggers (LinkedIn does not state the reason)

1. **Datacenter egress IP.** This host exits through a Frankfurt/VPS-range IP
   (AS215730 H2NEXUS CLOUD SERVICES = hosting/VPS range), not a residential connection. Automated risk scoring
   treats hosting IPs as bot traffic.
2. **New/low-activity account + API-only publishing.** Four posts in four days, all created through
   the official API from a third-party app, with no browser activity between them, matches a bot
   publishing pattern.
3. **Country mismatch.** The account is in a Russian-locale region while every session and API call
   comes from a German datacenter IP. Geographic inconsistency is a standard identity-check trigger.
4. **Automated side traffic.** Repeated OAuth authorisations, API probes and one rejected GET
   (`/rest/posts` -> 403) in a short window add "automation" signals.
5. It is NOT an obvious rules violation: publishing through the official Posts API with a user
   OAuth token and `w_member_social` scope is a supported, documented flow. The block looks like
   risk-scoring / identity verification, not a policy strike.

## Actions taken here

- Daily cron job `9fc7abfe87a3` (LinkedIn daily post cycle) PAUSED 2026-09-27 21:06 MSK so no further
  API calls hit a restricted account.
- No further API traffic (no retry loop, no browser automation, no cookie reuse).

## What to do (in order)

1. **Check the mailbox** for the LinkedIn notices: "You've got a new sign-in", "unusual activity",
   or the restriction notice itself. Those name the exact trigger time and place.
2. **Do not upload an ID immediately.** First use the appeal path: LinkedIn Help Center ->
   "My account has been restricted" -> appeal / contact support. For a newish account with modest
   content, appeals sometimes restore access without documents.
3. **If you do verify:** upload the document only in the official linkedin.com form over HTTPS,
   from the same browser/network you will keep using. Never send it to a third party and do not
   keep local copies (this machine stores none).
4. **After restore:** enable 2FA, change the password, and keep one stable network profile.
5. **If you decide to create a new account instead:** the same signal set will apply, so first fix
   the environment: residential-like IP, browser sessions alongside API posts, fewer posts at the
   start, no burst of automated actions.
6. **Token note:** the OAuth access token (valid until 2026-11-22) is worthless while the account is
   restricted - do not waste attempts on it. Re-auth only after access is back.

## Re-entry checklist before resuming the cron job

- [ ] account accessible in the browser
- [ ] password changed + 2FA enabled
- [ ] publishing environment stable (same IP/region as normal browsing)
- [ ] API token re-issued if needed (see REAUTH_NEEDED flow)
- [ ] first 2-3 posts published manually from the browser UI, then hand back to the API
- [ ] cron job unpaused (`cronjob action=resume job_id=9fc7abfe87a3`)

## UPDATE 2026-09-28 - RESOLVED (account unblocked)

- User confirmed 2026-09-28 the restriction has been lifted (appeal/verification completed on the user side).
- Verified live: GET /v2/userinfo with the existing token returned HTTP 200 (author sub read from
  userinfo) - API access works.
- The queued 2026-09-28 post (Wireshark 4.6.9) published via official Posts API: HTTP 201, urn:li:share:7510387200140693504.
- Cron job 9fc7abfe87a3 resumed (scheduled, pinned to custom/deepseek-v4-flash-0731).
- Remaining re-entry hygiene (optional, per skill): password change + 2FA, keep a stable network profile, consider fewer posts in the first days after restore.

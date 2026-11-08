# LinkedIn daily posting - two tracks

**Owner:** S960b
**Live workspace:** `~/linkedin/` on the Kali host (`/home/kali`)
**Last restructure:** 2026-11-08 - the folder is split into a shared core, the
Hermes track (legacy, now fallback) and the MultiTool track (current).

One English post a day, 20:00 MSK, published only through the official LinkedIn
Posts API (OAuth, `w_member_social`). Content rules live in one contract
(`shared/skill/SKILL.md`); the trigger and the agent that runs it differ per track.

---

## 1. The two tracks

| | **MultiTool track (current / primary)** | **Hermes track (fallback)** |
|---|---|---|
| Trigger | in-app plugin `multitool/plugin/linkedin-daily.mjs`, ticker inside the app, 20:00 MSK | OS cron `30 20 * * * ~/linkedin/linkedin_resilient.sh` -> `hermes cron run 9fc7abfe87a3` |
| Who writes the post | MultiTool agent session, created and prompted by the plugin | Hermes agent, job `9fc7abfe87a3` |
| Content contract | `shared/skill/SKILL.md` | same file, installed into `~/.hermes/skills/social-media/linkedin-content-research/` |
| Prompt | `prompts/cycle-routine.txt` (injected by the plugin) | same text, stored in the Hermes job |
| Needs the app running | yes | no (Hermes CLI works without the gateway) |
| Restore guide | `multitool/README.md` | `hermes/README.md` |

They are deliberately not exclusive: the plugin fires at 20:00, the OS cron at
20:30, and both consult `~/linkedin/PUBLISHED.md` (the plugin additionally uses
`~/linkedin/.claims/`), so whichever runs second sees "today already published"
and exits. Last real Hermes save: 2026-11-04, when the app was closed at 20:00 and
the Hermes CLI published at 20:43.

Publishing itself is identical in both tracks: the shared `publish_queued.py`
takes the ready post, escapes LinkedIn "little" text, calls
`POST https://api.linkedin.com/rest/posts`, and the day is logged in
`~/linkedin/PUBLISHED.md`.

---

## 2. Folder map

```
linkedin/
  README.md                      this file
  prompts/
    cycle-routine.txt            the daily cycle prompt (both tracks)
    token-reauth-reminder.txt    OAuth token reminder prompt (Hermes job 1693af0de3c1)
  shared/
    skill/SKILL.md               the content contract (v2, 2026-10-16) - single source of truth
    scripts/
      publish_queued.py          publish one QUEUE/<date>.md post via the official API
      publish_image_post.py      same, with an image asset
      oauth_callback.py          local OAuth callback server (port 8444)
      tg_report.py               Telegram report (reads bot token/chat id from .credentials)
      _html2text.py              tiny HTML -> text helper for research
      check_post.py              pre-publish sanity: raw length, em dashes, first lines
    incidents/ACCOUNT_RESTRICTED.md   2026-09-27 restriction + resolution record
  hermes/
    README.md                    full Hermes restore guide (fallback track)
    scripts/linkedin_resilient.sh  OS-cron wrapper v2 (publishes from QUEUE, retries with backoff)
  multitool/
    README.md                    full MultiTool description + restore guide + restore prompt
    plugin/linkedin-daily.mjs    the scheduler plugin (runs inside the app)
    config/opencode.json.snippet.json  the plugin block to paste into the app config
```

Live, machine-specific files stay OUT of git: `~/linkedin/.credentials`,
`PUBLISHED.md`, `QUEUE/`, `PUBLISHED/`, `DRAFTS/`, `IMAGES/`, `RESEARCH/`,
`BACKLOG.md`, `auto-post.log`, `.claims/`.

---

## 3. What changed in the 2026-11-08 restructure

- Hermes-era files moved under `hermes/` and `shared/`; the old single README
  became `hermes/README.md` (paths updated, cron time corrected to 20:30).
- `linkedin/skill/SKILL.md` (v1, 14.10) replaced by live v2 from the Hermes skill
  store, with the Telegram bot username and chat id masked back out.
- `linkedin_resilient.sh` synced from v1 to the live v2 (QUEUE publish path).
- New: `multitool/` - the plugin that has actually been triggering the cycle since
  2026-11-02, the app-config snippet, and a restore guide with a ready prompt.
- New: `shared/scripts/check_post.py` (existed only on the host).
- `prompts/cycle-routine.txt` is now the single copy of the cycle prompt, read by
  both tracks; the plugin looks for it in `~/linkedin/` first, then in this repo.

---

## 4. Non-negotiable rules (both tracks)

- Official LinkedIn Posts API only. No browser automation, no cookie reuse, no
  scraping, no unofficial integrations. If the channel is unavailable, the post
  goes to `~/linkedin/QUEUE/` and a failure is logged - never bypass.
- Secrets never enter this repo: `.credentials`, access tokens, the member `sub`,
  the Telegram bot username and chat id are masked or omitted.
- The personal-fact gate in `shared/skill/SKILL.md` is the hard limit: only
  CONFIRMED background may appear as personal experience.

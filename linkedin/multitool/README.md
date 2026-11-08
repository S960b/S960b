# MultiTool track - daily LinkedIn cycle inside the app

**Repo path:** `linkedin/multitool/`
**Live plugin:** `~/.config/gigatool/plugins/linkedin-daily.mjs`
**Live workspace:** `~/linkedin/`
**In production since:** 2026-11-02 (replaced the Hermes cron as the primary trigger)

This is the current way the daily post happens. No external cron, no Hermes job,
no gateway: a plugin loaded by the MultiTool app itself waits until 20:00 MSK,
creates a fresh agent session and injects the cycle prompt into it. From there the
normal agent works: research, gates, drafting, publishing through the official
LinkedIn API, logging, Telegram report.

---

## 1. How it works (exact mechanics)

```
20:00 MSK
  app plugin tick (every 60 s)
    -> todayCovered()?  PUBLISHED.md has "## <date>" or "DATE: <date>"  -> skip
    -> claimToday()     create ~/linkedin/.claims/<date> with O_EXCL  -> only one copy wins
    -> acquireLock()    ~/linkedin/.auto-post.lock (TTL 6 h)
    -> read prompt      ~/linkedin/CYCLE_PROMPT.md, else this repo's prompts/cycle-routine.txt
    -> client.session.create({title:"LinkedIn cycle <date>"})
    -> client.session.prompt(<prompt>)
  agent session (normal MultiTool agent, full toolset)
    research -> gates -> scoring -> draft -> final review
    -> python3 ~/linkedin/publish_queued.py ~/linkedin/QUEUE/<date>.md   (dry-run first)
    -> PUBLISHED.md entry + PUBLISHED/<date>.md + Telegram report
```

Why the extra guards:

- **Every chat window loads its own copy of the plugin.** With several chats open
  there are several independent timers; `.claims/<date>` is created with
  `O_CREAT|O_EXCL`, so exactly one instance wins and the rest log
  `skip: another plugin instance already claimed today's run`.
- **`todayCovered()`** matches only a real post date - `## 2026-11-08` or
  `DATE: 2026-11-08` - not any date-like string, because a CISA deadline quoted
  inside an older post would otherwise close the current day.
- The 6-hour lock file covers a run that is still going when the next tick fires.

## 2. Plugin options

Passed as the second element of the plugin entry in the app config:

| Option | Default | Meaning |
|---|---|---|
| `dry_run` | `true` | When not explicitly `false`, the plugin only writes what it *would* do into the log. **Safe default - keep `false` only in the real config.** |
| `force` | `false` | Ignore the "today already covered" check (manual use only). |
| `tick_ms` | `60000` | Ticker period. |
| `log_file` | `~/linkedin/auto-post.log` | Override the log target (used for testing). |

Manual run: create the trigger file, the next tick picks it up and deletes it.

```bash
touch ~/linkedin/.run-now
```

## 3. Live files

| Path | Role |
|---|---|
| `~/.config/gigatool/opencode.json` | app config; must contain the plugin entry (see `config/opencode.json.snippet.json`) |
| `~/.config/gigatool/plugins/linkedin-daily.mjs` | the plugin itself (copy of `plugin/linkedin-daily.mjs` here) |
| `~/linkedin/CYCLE_PROMPT.md` | prompt the plugin injects (copy of `../prompts/cycle-routine.txt`) |
| `~/linkedin/auto-post.log` | plugin log: load, run start, skips, session id, errors |
| `~/linkedin/.claims/` | one file per claimed day |
| `~/linkedin/.auto-post.lock` | run lock |
| `~/linkedin/.run-now` | manual trigger marker |
| `~/linkedin/.credentials` | CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, TOKEN_EXPIRES_AT, TELEGRAM_* - **never in git** |
| `~/linkedin/publish_queued.py`, `tg_report.py` | shared scripts copied from `../shared/scripts/` |

## 4. Restore from zero (new host / reinstall)

1. **Workspace**

   ```bash
   mkdir -p ~/linkedin/QUEUE ~/linkedin/PUBLISHED ~/linkedin/DRAFTS ~/linkedin/RESEARCH
   cp <repo>/linkedin/shared/scripts/. ~/linkedin/
   chmod +x ~/linkedin/*.py
   cp <repo>/linkedin/prompts/cycle-routine.txt ~/linkedin/CYCLE_PROMPT.md
   ```

2. **Credentials** (`~/linkedin/.credentials`, never committed)

   ```
   CLIENT_ID=...
   CLIENT_SECRET=...
   ACCESS_TOKEN=...
   TOKEN_EXPIRES_AT=YYYY-MM-DD
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```

   If the token is missing or expired: `python3 ~/linkedin/oauth_callback.py`
   (port 8444; 8443/9443 are taken on this host), open the URL from
   `~/linkedin/auth_url.txt` in `/home/kali/firefox/firefox-bin`, complete consent,
   the tokens are written back automatically. `offline_access` is rejected for this
   app, so the token lives ~60 days and re-auth is manual.

3. **Content contract.** Copy `../shared/skill/SKILL.md` to
   `~/.config/gigatool/skills/linkedin-content-research/SKILL.md` so the agent can
   load it as a skill. If it is not installed there, the cycle still works, but the
   agent has to read the file from the Hermes path and says so in its log
   ("skill not registered as loadable") - that is the current state, see §6.

4. **Plugin**

   ```bash
   cp <repo>/linkedin/multitool/plugin/linkedin-daily.mjs \
      ~/.config/gigatool/plugins/linkedin-daily.mjs
   ```

   Then paste the block from `config/opencode.json.snippet.json` into the
   `plugin` array of `~/.config/gigatool/opencode.json` (keep the existing entries)
   and restart the app.

5. **Verify**

   ```bash
   tail -5 ~/linkedin/auto-post.log     # expect "plugin loaded; dry_run=false; ..."
   touch ~/linkedin/.run-now            # expect "run start (manual trigger file)"
   node --check ~/.config/gigatool/plugins/linkedin-daily.mjs
   ```

   Do a dry-run publish before the first live one:

   ```bash
   python3 ~/linkedin/publish_queued.py ~/linkedin/QUEUE/<date>.md --dry-run
   ```

## 5. Restore prompt (paste this into a fresh agent)

> Restore the MultiTool-side LinkedIn daily posting on this host. Read
> `~/S960b/linkedin/multitool/README.md`, `~/S960b/linkedin/prompts/cycle-routine.txt`
> and `~/S960b/linkedin/shared/skill/SKILL.md` first.
> 1) Make sure `~/linkedin/` has the shared scripts (`publish_queued.py`,
> `tg_report.py`, `oauth_callback.py`, `check_post.py`), `CYCLE_PROMPT.md`,
> `QUEUE/`, `PUBLISHED/`, `RESEARCH/` and `.credentials` with a live access token.
> 2) Copy `linkedin-daily.mjs` into `~/.config/gigatool/plugins/` and add its entry
> from `multitool/config/opencode.json.snippet.json` to the `plugin` array in
> `~/.config/gigatool/opencode.json`, keeping the entries already there.
> 3) Install the skill to `~/.config/gigatool/skills/linkedin-content-research/SKILL.md`.
> 4) Check the token with `GET https://api.linkedin.com/v2/userinfo`, then verify the
> plugin loaded in `~/linkedin/auto-post.log` and trigger one cycle with
> `touch ~/linkedin/.run-now`.
> 5) Do not publish anything during the restore itself. Report what is missing.

## 6. Known gaps / roadmap

- **The skill is not registered in the app.** It lives only in the Hermes skill
  store; MultiTool sessions read the file by path and log that the skill is not
  loadable. Installing a copy under `~/.config/gigatool/skills/` fixes it (§4.3).
- **No token reminder inside the app.** The old reminder was Hermes cron job
  `1693af0de3c1` (2026-11-17), which cannot fire - the Hermes gateway is not
  running. The cycle itself writes `~/linkedin/REAUTH_NEEDED.md` and reports to
  Telegram when `TOKEN_EXPIRES_AT` has passed, so expiry cannot pass silently, but
  there is no 5-day early warning any more. Token expires **2026-11-22**.
- The plugin depends on the repo prompt file only as a fallback; if the repo is not
  cloned, `~/linkedin/CYCLE_PROMPT.md` alone is enough.
- The plugin has no catch-up: if the app is closed at 20:00, the day is saved by the
  Hermes fallback at 20:30 or not at all.

## 7. Troubleshooting

| Log line | Meaning |
|---|---|
| `plugin loaded; dry_run=true ...` | plugin is registered but will not run - add `{ "dry_run": false }` |
| `skip: today's post already covered` | `PUBLISHED.md` already has today's date |
| `skip: another plugin instance already claimed today's run` | normal when several chats are open |
| `skip: another run holds the lock` | a run is still in progress, or a stale lock (<6 h old) |
| `error: cannot read prompt file ...` | neither `~/linkedin/CYCLE_PROMPT.md` nor the repo prompt exists |
| `error: SDK client without session.create/prompt` | app version without the session SDK - the plugin cannot inject |

# NEWS v2.1 — dated timeline

All times UTC. Every entry is evidenced by a file in `data/`.

| when | what | evidence |
|---|---|---|
| 2026-11-15 | free-source intake built: BLS lane, HTTP pollers, Bluesky Jetstream (push), taxonomy and freshness rules | `source/v21_http_intake.py`, `source/v21_bluesky_listener.py` |
| 2026-11-16 08:37Z | plugin first loaded in the desktop runtime, `paper=true`, no model call made | plugin log (not published, quoted in MT report seq72) |
| 09:37Z | five long-lived contexts, `client=present` | report seq82 |
| 10:01:36Z | **first genuine DeepSeek exchange**, probe: `{"probe":"ok"}`, 20 051 in / 13 out tokens | `data/export_*.json` → `gateway_usage` |
| 10:02:38Z | **pair 1 attempted and refused** (`40d21dd674c8`): parser demanded a whole-string JSON, the model had written prose first. 2 calls spent, no record | `data/export_*.json` → `refusals[0]` |
| 10:13:35Z | **pair 2 recorded** (`e0c7b377a018`, cointelegraph, `context_news`): N 0.50/0 bps → FLAT, NM 0.45/−15 bps → FLAT, entry 64 408.49 | `data/export_*.json` → `paper` |
| 10:31:02Z | **pair 3 refused** (`d619c25574da`): NM arm returned a **truncated** object (no closing quote/brace). Correctly refused, not retried | `refusals`, report seq92 |
| 10:41:59Z | runtime reloaded; restricted-agent code live | report seq90 |
| 10:53:35Z | **pair 4 recorded with `agent: news-restricted` and `tools: 0`** (`f89bc542ffdd`): N 0.45/−95 → SHORT_PAPER, NM 0.34/−85 → SHORT_PAPER | `data/export_*.json` → `paper`, `gateway_usage` |
| 14:13:35Z | pair 2 label due (pending at packaging time) | `paper.outcomes` empty |
| 14:53:35Z | pair 4 label due | — |

## What changed as a result of each finding

1. **Tool leakage** — every pilot arm session invoked `websearch` (6 calls
   total), i.e. the model could read prices after the cutoff. Fix: a dedicated
   `news-restricted` agent denying every tool, applied at session creation; a
   session that cannot be created that way is refused rather than downgraded.
   Proven live at 10:53Z: `agent='news-restricted'`, `tools=0`, and the two arms
   consumed only 329 and 433 input tokens instead of ~20 000.
2. **Reservations ≠ completions** — added a read-only reconciliation from the
   gateway (this bundle's `gateway_usage`), plus a per-pair circuit breaker
   (8 completions / 400k input tokens / $0.20) that refuses to seal a runaway.
3. **Parser tolerance, then fail-closed** — the parser now accepts prose around
   one decision object but refuses two independently valid objects, which would
   be injection-shaped.
4. **Selection could starve** — the selector was asked for one candidate and
   kept re-offering an already-reserved story; it now takes a batch and skips
   reserved events.
5. **Silent refusals** — refusals now leave a trace, which is why pair 1 and
   pair 3 are diagnosable instead of invisible.

## 2026-11-16T13:40Z — tool isolation FAILED; inference paused

An independent check of every tool part in the NEWS sessions showed that the
`news-restricted` agent does **not** prevent tool use. Live evidence:

```
websearch  pilot sessions       5
websearch  restricted sessions  2   (df3eb36487c1, c377be7226e9)
memory     restricted session   1
skill      restricted session   1
```

Root cause found by inspection of the shipped runtime bundle
(`app.asar` → `out/main/application-*.js`): the desktop client's session-create
call is `POST /session {method:"POST", body:{title}, directory}`
(`createSession(directory, title)`), so an `agent` field passed at session
creation is **not part of the documented payload**. Agent selection therefore
does not reach the enforcement point, and the permission block in the agent file
never gates the tool registry. The `permission` map is used by the app for its
own ask/allow/deny prompts (`STRICT_PERMISSION` / `BALANCED_PERMISSION`), which
is a different mechanism from disabling a tool for a session.

Actions taken:

1. **Automatic model inference paused.** `paper_enabled=false`,
   `auto_enabled=false` in the plugin config (labels stay on, they cost no model
   call) and a hard file flag
   `~/.local/share/news-phase4-v21/PAUSED_MODEL_INFERENCE`, checked by
   `v21_cycle.py prepare` before anything else. Verified: `prepare` returns
   `{"status":"blocked","reason":"model_inference_paused"}`.
2. **Preflight probe written** (`scripts/v21_toolpreflight.py`): it asks the
   model to *use* a tool explicitly and then reads the gateway record — a refused
   call leaves a tool part with `status:"error"`, an executed call leaves
   `running`/`completed`. Exit 0 = denial proven, 3 = leak, 4 = inconclusive.
   It could not be run against the live plugin yet: the running instance was
   started before the probe gained the ability to pass custom text/agent, so it
   answered with the old canned prompt. Blocked on one reload.
3. **Post-hoc arm audit** in the runner: after each arm answers, the session's
   messages are read and any tool part aborts the pair with
   `v2.1_tool_used_in_arm_no_seal`. Detection cannot undo a leak, but it stops a
   contaminated pair from being sealed as clean.

**Status: tool isolation FAILED / PENDING. No pair may be called audit-grade, and
H1–H5 remain blocked.**

## 2026-11-16T14:10Z — prompt-path investigation

Checked whether the per-prompt call could carry the isolation the session call
cannot. The shipped client sends:

```js
prompt(directory, sessionID, text, hint, model) {
  const parts = [...hint ? [{type:"text",text:hint,synthetic:true}] : [],
                 {type:"text", text}];
  return this.req(`/session/${encodeURIComponent(sessionID)}/prompt_async`, {
    method: "POST",
    body: { parts, ...model ? { model } : {} },
    directory, ...
  });
}
```

So the prompt payload carries exactly `parts` and optionally `model`. There is
**no `agent` and no `tools` field** in this fork's request shape, and I found no
server-side handler that would accept them. Passing extra fields would be a
guess, not a supported mechanism, so I have not shipped it.

`session.permission` exists as a column but nothing in the readable client code
sets it, and the config schema exposes `steps`/`maxSteps` (agentic iterations),
not a tool registry filter.

**Consequence: no supported route to a tool-free generation is demonstrated on
this build.** Per seq99's standard — enforcement at the generation boundary or a
permission-layer refusal proving the tool did not execute — the honest verdict is
**NO-GO for tool isolation**, and inference stays paused.

**Also fixed here:** the published `source/v21_paper_runner.mjs`,
`source/v21_plugin.mjs` and `source/v21_service.mjs` still imported helpers by an
absolute `/home/kali/...` path (my clean-clone tests only passed because that
folder exists on this machine). All three now resolve relatively, `ROOT` comes
from `NEWS_V21_ROOT` or the module directory, and the Node suite runs from a
sandbox with a throwaway `HOME` and no access to the old directory.

## 2026-11-16T14:25Z — first 4h label settled, and a silent schema bug found

`e0c7b377a018` became due at 14:13:35Z and, at the 14:24 tick, `v21_outcomes`
reported `settled=0` with no error visible in the log. The cause was not the
anchor: `paper_outcomes` had been created **before** `due_ms`, `drift_ms` and
`method` existed, and `CREATE TABLE IF NOT EXISTS` leaves an old table alone, so
the insert failed on a missing column on every tick.

Fixed by `migrate_records()` in `open_records()`: missing columns are added with
`ALTER TABLE`, existing rows preserved, operation idempotent. Two regression
tests cover the in-place migration and repeated opening.

The label then settled on its own terms:

```
event     e0c7b377a018
due       14:13:35.304Z
exit      14:13:59.999Z      (first 1m close at or after due)
drift     24 695 ms  ≤ 60 s
method    binance_public_1m_close
entry     64 408.49 → exit 64 239.98 = −26.2 bps
paper     news FLAT → 0.0 bps net, news_market FLAT → 0.0 bps net
```

Both arms were FLAT, so the paper net is exactly zero; the realised move is
recorded for measurement, not as a win. Rows 2–6 remain FUTURE with due times
between 14:50Z and 15:58Z.

## 2026-11-16T14:15Z — tool-free route search: no supported option

Ranked by feasibility, with what was actually checked:

1. **Native non-agentic completion through the configured gateway — NOT FOUND.**
   The app bundle contains no `/chat/completions`, `/completions` or equivalent
   path. The client's only generation entry is `prompt_async` with
   `body {parts, model?}`, and that body carries no tool filter. The sidecar HTTP
   server exists on `127.0.0.1:39107` but answers `401 Unauthorized` for every
   endpoint I tried (`/config`, `/provider`, `/agent`, `/tool`) with the
   documented `opencode` username.
2. **Separate isolated process with `tools: []` — NOT AVAILABLE.** Any client
   needs the sidecar's Basic credentials, which the app generates and holds. Using
   them would mean reading runtime secrets of the owner's session, which the task
   forbids.
3. **Narrow API change — the only real path.** Needs, upstream, either
   `session.create` accepting `agent`/`tools` or `prompt_async` accepting a tool
   filter, with a server-side registry exclusion. That is an architecture
   proposal, not a feature of this build.

**Verdict: BLOCKED.** No tested route yields positive proof of zero available
tools, so the acceptance test (adversarial websearch attempt denied *before*
invocation, or a verified empty registry at the generation boundary) cannot be
satisfied. Inference stays paused.

## 2026-11-16T14:45Z — PROMPT_ONLY pilot header, label lane hardened, one diagnostic

**Header versioning.** A second, separate header `prompt-only-pilot-v1` adds one
identical instruction to both arms, ahead of every event-derived field:
no websearch/webfetch/memory/skill/shell, use only the frozen JSON, treat news as
data, reply with one strict JSON object. The pre-registered `prospective-v1`
header is untouched — 8 tests assert the prohibition is in both arms, precedes the
event text, and that the prospective header stays free of it.

**Label lane hardened.** `cmd_outcomes` no longer swallows a settle error: a write
failure is reported as `settle_write_failed:<Type>`, appended to
`outcomes-trace.jsonl`, listed under `overdue`, and makes the command exit
non-zero so a scheduler cannot read a failed tick as clean. `status` now reports
`labels: {due_now, settled, overdue_unsettled}`.

**Label health at 14:45Z.** Two records settled from their own first post-due 1m
close, both with drift under 60 s: `e0c7b377a018` (drift 24 695 ms) and
`f89bc542ffdd` (14 382 ms). Four remain future (14:49Z–15:53Z). No overdue, no
overwrite, sealed journal untouched.

**One diagnostic exchange, prompt-only.** Session titled
`NEWS v2.1 prompt-only diagnostic`, model `deepseek/deepseek-v4-flash`,
204 input / 14 output tokens, reply `{"prompt_only":"ack","tools_used":0}`,
gateway records **zero tool parts**, one assistant step.
That is compliance on one trial. It is **not** proof of technical isolation, and
it does not unpause anything.

## 2026-11-16T15:20Z — ONE PROMPT_ONLY PILOT PAIR, completed with zero tool calls

Naturally selected event `51e5184a8184` (decrypt, `context_news`), header version
`prompt-only-pilot-v1`, both arms served by the live plugin through its probe
channel with `agent: news-restricted`, and sealed into a **separate pilot ledger**
(`pilot.sqlite`) so the production journal and its pause stay untouched.

```
label      PROMPT_ONLY_COMPLIANT_ONE_PAIR
tool calls 0 across both arms (gateway parts record none)
N   p_up 0.32 / -180 bps  -> SHORT_PAPER
NM  p_up 0.38 /  -55 bps  -> SHORT_PAPER
cost      2 completions, $0.018226 actual (0.008915 + 0.009311)
tokens    19 951 in / 60 out (N), 20 819 in / 79 out (NM)
quote     sampled after both arms; entry recorded in the pilot ledger
```

The arms consumed ~20 k input tokens each — the same magnitude as the earlier
contaminated sessions — which is consistent with the model still having tools
available and simply not using them. That is precisely why the label says
COMPLIANT and not ISOLATED.

**Label-lane regression found and fixed.** While waiting for the pilot, the
production label loop was found dead: with `paper_enabled=false`, `tick()`
returned before the label branch, so pausing inference silently stopped outcomes
too. Labels now run regardless of the inference flag, and "the covering minute
has not closed yet" is reported as pending rather than as a failure, so a normal
60-second wait no longer raises a false overdue alarm.

**Settled production labels at this checkpoint** (`labels.settled = 3`):
`e0c7b377a018`, `f89bc542ffdd`, and `df3eb36487c1`. Remaining due times
15:23Z / 15:38Z / 15:53Z were still future; `overdue_unsettled` empty.

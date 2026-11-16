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

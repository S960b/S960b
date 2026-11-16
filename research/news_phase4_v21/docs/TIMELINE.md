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

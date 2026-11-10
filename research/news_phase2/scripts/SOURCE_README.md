# NEWS Intelligence Phase 2 — native MultiTool session adapter

**Status:** ChatGPT-authored handoff candidate (2026-11-06). **Manual-only by default.** No live trading, no private Binance access, no direct DeepSeek key, no Hermes Agent CLI. This package does **not** modify the established `news_phase2.py` or its `test_news_phase2.py`.

The local MultiTool (MT) client already has the DeepSeek gateway. This adapter uses `client.session.create` + `client.session.prompt` from MT SDK, patterned after the published `packages/mt_plugin_reference/linkedin-daily.mjs`. MT's configured/default model is expected to be `deepseek-v4-flash`; check its actual identity during the synthetic probe and do not infer it from the adapter's label alone.

## Files to install beside the original baseline

Copy exactly these five files into `/home/kali/research/news-phase2/`, without replacing the existing `news_phase2.py`:

- `news_mt_cycle.py` — Python one-shot phases: `prepare`, `session`, `prompt`, `finalize`, `labels`, `status`, `abandon`
- `news-mt-plugin.mjs` — native MT plugin; manual trigger watcher, **no periodic news forecast schedule**
- `test_news_mt_cycle.py` — Python offline tests
- `test_news_mt_plugin.mjs` — Node offline tests
- `README.md` — this document (may be saved as `NEWS_MT_SESSION_README.md` in package root)

The previous experimental `news_mt_adapter.py` was based on an unrelated Hermes Agent CLI and is **obsolete**. Retain an immutable backup and do not invoke it; do not need to change, reinstall or adjust Hermes.

## Data flow

1. `prepare`: lock the same `<db>.lock` as baseline; verify Binance clock; label already-due outcomes; collect fixed public RSS sources; preserve first-tick warmup and 4h/6-per-day/60-total limits; select only eligible post-warmup headlines; persist one `mt_batches` record and a `model_attempt` event **before** external model invocation. Return bounded prompt and batch ID.
2. MT plugin creates a session with `client.session.create`; Python records that `session_id`; Python transitions the record to `prompt_attempted` *before* the single `client.session.prompt` call. Prompt includes only RSS text marked as untrusted data.
3. Plugin extracts the assistant's response from the SDK result or, if necessary, reads `client.session.messages`. It passes JSON `{session_id,response}` to `finalize` **via stdin**, not a shell command string.
4. `finalize`: verify assigned session, pending state, freshness (15 minutes), strict output keys and values using baseline `parse_prediction`; **only after inference** get Binance time and public BTC quote; insert one `predictions` record and link selected stories atomically. No retrospective prediction.
5. Later `labels` follows the baseline's 1h/4h/24h candle scoring, and `status` includes baseline `report`. Labels are observations attached to one prediction, not three independent forecasts.

Any ambiguous SDK/network/timeout response leaves the batch **pending**; it is never automatically reissued. Inspect `status`, MT session and logs before a manual reconciliation. Explicit `abandon --batch-id ... --confirm-abandon` permanently releases the pending reservation **without refunding the model-call budget**, after confirming there will be no valid later model result. It is not a retry.

## Local offline checks

From the installed package directory (same as baseline):

```sh
python3 -m py_compile news_mt_cycle.py test_news_mt_cycle.py
node --check news-mt-plugin.mjs
python3 -m unittest -v test_news_mt_cycle.py
node --test test_news_mt_plugin.mjs
python3 -m unittest -v test_news_phase2.py
```

Tests in this package mock RSS/Binance/MT and do not access live APIs. Original baseline tests should still pass unchanged (10 cases). See handoff report for verified test counts before install.

## Plugin registration: safe initial config

The existing MT plugin loader supports entries shaped as `plugin: [[absolutePath, options]]`. Check the live config and installed loader first; **merge a single entry**, never replace the whole plugin list. Conceptual example:

```json
[
  "/home/kali/research/news-phase2/news-mt-plugin.mjs",
  {
    "root": "/home/kali/research/news-phase2",
    "directory": "/home/kali/research/news-phase2",
    "python": "python3",
    "dry_run": true,
    "poll_ms": 30000
  }
]
```

Do not enable any `news-phase2.timer`, systemd unit, cron or automatic forecast schedule. The interval above **only watches for explicit manual trigger files**, not for news. A plugin reload may be required by the local MT host; check it safely without disrupting existing conversations.

### Manual sequence

1. With `dry_run:true`, create `/home/kali/research/news-phase2/.mt-news-probe-now` and check the plugin log: it must say `dry_run`, with no MT session/model use. Same for `.mt-news-run-now`.
2. After offline checks and recording the config backup, set **only** this plugin's `dry_run:false` for a single approved manual test. Create `.mt-news-probe-now`. The plugin asks the MT model about **synthetic** headlines, checks exact JSON and logs `probe_ok`. This does not read/write NEWS SQLite and is *not* a BTC prediction.
3. Create `.mt-news-run-now` once. The first `prepare` against a new DB is only warmup (`first_collection_warmup`); no model call. On an existing warmed-up DB, it may skip for lack of eligible news. Do not fabricate qualifying RSS stories or backfill a forecast just to force a response.
4. When genuinely new qualifying headlines appear, another *manual* `.mt-news-run-now` may create one prediction. Check `news_mt_cycle.py status` and the SQLite read-only summary, one prediction per batch. A subsequent manual `labels` call updates only due horizons. Report exact clock skew, RSS success, batch status, session ID, JSON validation, entry quote/time and prediction count (no secrets, do not copy user data into Git).
5. Keep the schedule **disabled** until the owner explicitly approves automatic collection.

```sh
cd /home/kali/research/news-phase2
python3 news_mt_cycle.py status --db ~/.local/share/news-phase2/news.sqlite
python3 news_mt_cycle.py labels --db ~/.local/share/news-phase2/news.sqlite
# One manual trigger, *only after* plugin installation:
touch /home/kali/research/news-phase2/.mt-news-probe-now
```

`prepare` is an effectful action: it persists a model-call reservation if a batch is eligible. **Do not invoke `prepare` manually on the production DB just to see what would happen.** Use the plugin trigger to perform the entire process. For dry-run, rely on `dry_run:true` or a disposable isolated DB.

## Compatibility and stop conditions

- Confirm MT SDK `session.create`, `session.prompt`, and if necessary `session.messages` response shapes. If assistant text cannot be extracted, **stop, preserve pending state**; report shape metadata only, no raw prompts/secret values, to ChatGPT.
- If the actual model is not `deepseek-v4-flash`, report before attributing predictions to DeepSeek. Do not create an API key; use the configured MT model.
- If the original baseline SHA, database schema or public feed endpoint differs from the pinned source, do not edit it silently; report exact differences.
- If any test, clock check, RSS collection, JSON validation or candidate state fails, don't force a real forecast. No trade/order/withdrawal API methods appear here.
- Config, app sessions and DB backups should remain private on Kali. Avoid logging model responses, credentials or full news payloads.

---

## v1.2 — опциональная автоматизация до первого прогноза

Опции конфига плагина (все опциональны, по умолчанию автоматизации нет):

| опция | по умолчанию | смысл |
|---|---|---|
| `auto_until_first_prediction` | `false` | включает авто-проверки |
| `auto_interval_ms` | `900000` | окно авто-проверки (15 мин) |
| `label_interval_ms` | `300000` | частота label-only проходов (5 мин) |
| `dry_run` | `true` | как и раньше: без RSS, БД и модели |

**Стоп — авторитетно из SQLite.** `auto_state()` считает `stopped = predictions >= 1`.
После первого подтверждённого прогноза никаких авто-инференсов больше не будет,
включая релоад плагина и перезапуск процесса, потому что условие читается из базы.

**Блокировки.** Перед любым побочным эффектом проверяются: (1) стоп по прогнозам,
(2) активный `mt_batches` в состоянии `prepared/session_created/prompt_attempted`.
При блокировке окно не портит бюджет: ни RSS, ни `model_attempt`. Повтора промпта нет.

**Один слот — один раз.** Таблица `auto_slots(slot_ms PRIMARY KEY)` + `INSERT OR IGNORE`
дают атомарный клейм и внутри процесса, и между инстансами плагина; файловый `flock`
на `<db>.lock` (уже есть в `database()`) сериализует запись в SQLite.

**Метки отделены от прогнозов.** `labels`/`label` используют только публичные свечи
Binance, модель не вызывают и бюджет не расходуют; ставятся не раньше +1/+4/+24 ч.

**Стоп-условия, которые не обходятся:** 4 ч каденция, ≤6 `model_attempt` за 24 ч,
≤60 прогнозов, один прогноз на батч, котировка Binance строго после инференса,
строгий JSON, никакого синтетического бэкфилла.


## v1.3.1 review candidate: stop idle label polling after completion

\`label_progress()\` is a read-only SQLite query that requires all 1h/4h/24h
outcome rows for EVERY saved prediction. Zero predictions is not considered
complete. \`labels\` reports \`pilot_complete=true\` on the tick that saves the
third outcome. The plugin then clears its own label timer immediately.
After a restart, the next label tick sees the same persisted outcome rows
and stops before a clock, network, or new claim. The auto-forecast timer
also clears after its final prediction; the SQLite stop remains authoritative.

The v1.3 claim allows just one *effective label processing* per five-minute
slot, though other MT plugin instances still launch Python briefly and
receive \`label_slot_already_claimed\`. On transient Binance failures, retry
occurs in a later five-minute slot, not indefinitely within the same slot.

**Deploy only after the live +24h outcome is durably saved and reviewed.**
Leave the installed v1.2 and production SQLite unchanged meanwhile.

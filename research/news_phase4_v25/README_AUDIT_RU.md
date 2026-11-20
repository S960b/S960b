# NEWS Intelligence v2.6 — audit pack

Пакет для независимого аудита сторонним ИИ. Собран из ветки
`news-v25-next100-chatgpt-20261119`, снимок на коммите
`059026bb66c42833cf0e23426ac1a99d976cd9dd`, без изменений алгоритмов.

- Ветка аудита: `audit/news-v26-rss14-multiasset-20261120`
- Базовый коммит: `059026bb66c42833cf0e23426ac1a99d976cd9dd`
- Развёрнутый код (host): `/home/kali/research/news_phase4_v26_canary`, `DEPLOYED_SHA` = тот же коммит
- sha256 установленных файлов **совпали** с исходной веткой: расхождений ноль
- systemd `ExecStart` указывает на `…/v26_series_runner.py --poll` из этой же копии

---

## 1. Что это за система

PAPER-ONLY исследование: предсказывает знак 4-часового изменения спота
BTCUSDT / ETHUSDT / SOLUSDT по новостному событию. Ни одного ордера, ни одного
ключа биржи, ни одного торгового API. Все цены — публичный `api.binance.com/api/v3`
(тикер и свечи), модели — DeepSeek через собственный raw-клиент.

Три «руки» промпта на каждое событие:
- `news` — только новость,
- `news_market` — новость + рынок,
- `market` — только рынок (контроль изоляции: новость туда не попадает).

---

## 2. Dataflow

```
14 публичных RSS
      │  ОДИН fetch на источник (v26_feed_pipeline.ingest_one)
      ▼
v24_collector.tick(adapter=…)
      ├── RAW: неизменяемые байты ответа → <raw_dir>/<дата>/<symbol>-<sha256>.bin
      ├── v24_events / v24_observations / v24_raw_manifest (логический архив)
      └── rss_sidecar.upsert → rss_metadata (summary, asset_scope, first_observed)
                ▼
      rss_noise_filter.decide → accepted / symbol / reason
                ▼
      v26_feed_pipeline.candidate → сверка 6 полей между БД + переразбор XML
                ▼
      v26_series_runner.execute_one ──► market: v26_market_provenance.observe("hourly")
                                        └ witness: request URL + SHA256 сырых байт
                ▼
      v26_asset_binding.bind_claim → event_id ↔ symbol, ЗАМОРОЖЕН до вызова модели
                ▼
      3 руки через tier2 / v22_arms → attestation.jsonl → seal_triple
                ▼
      tier2_label_watch через +4 ч → tier2_outcomes (закрытая 1m свеча выбранного symbol)
```

---

## 3. Live-статус: что реально работает

**Работает:** отдельный RSS-only canary, `news-v26-rss-canary.timer`, раз в минуту,
`v26_series_runner.py --poll`. 14 фидов, ноль вызовов модели, ноль активации.

**Не работает и не активировано:** запуск серии, вызовы DeepSeek в рамках v2.6,
`--execute`, любые сделки. `TIER2_V26_ACTIVATION.json` и `V26_TRIGGER_ENABLED`
в v2.6 state **отсутствуют**.

v2.5 (живой production) не тронут: release `1c5b154aa090286c13576f3ff5e8b0b4d87fff19`,
trial-таймеры `inactive/disabled`, серия 100/300 — 0 claims / 0 invocations / 0 labels.

---

## 4. 100/300 — это протокол исследования, а не лимит модели

Серия спроектирована на **100 событий × 3 руки = 300 вызовов**. Это
предрегистрированный объём наблюдения, а не ограничение возможностей модели и не
тариф. Гейт (`SERIES_TRIGGER_ENABLED`) в v2.5 остаётся выключенным.

---

## 5. Таблицы и связи

`rss.sqlite` (сырьё):
- `v24_events(content_sha256 PK, source_id, category, provenance, title, origin_url, published_ms, observed_ms, raw_sha256, terms_decision, first_seen_ms)`
- `v24_observations(occurrence_id PK, semantic_cluster_id, …, raw_path, first_seen_ms)`
- `v24_raw_manifest(raw_sha256 PK, source_id, url, http_status, observed_ms, bytes, path, verified_ms)`
- `v24_fetch_log` — построчный журнал запросов

`rss_meta.sqlite` (метаданные): `rss_metadata(occurrence_id PK, source_id, title, origin_url, published_ms, first_observed_ms, last_observed_ms, asset_scope, summary, raw_sha256, category, provenance)`

`series.sqlite` (серия, пока не создаётся в v2.6): `series_claims`, `series_invocations`,
`series_labels`, `tier2_triples`, `tier2_outcomes`, `tier2_arms`, `tier2_bindings`,
`series_asset_bindings`, `series_market_witnesses`.

Связи: `v24_events.content_sha256 = v24_observations.occurrence_id = rss_metadata.occurrence_id
= series_asset_bindings.event_id`; `v24_events.raw_sha256 = v24_raw_manifest.raw_sha256`.

---

## 6. Условия допуска и отказа

Кандидат допускается только если выполнено **всё**:

1. `source_id` входит в roster из 14;
2. `published ≤ observed ≤ first_observed ≤ now`;
3. `manifest.url == roster url`, `manifest.http_status == 200`;
4. `raw_sha256` в событиях = в наблюдениях = в манифесте = sha256 файла на диске;
5. `observed_ms` совпадает во всех трёх таблицах, `raw_path` совпадает;
6. `occurrence_id == sha256(source_id|title|published_ms)`;
7. sidecar-строка совпадает по всем полям, `asset_scope` = из roster;
8. `rss_noise_filter.decide(...)` вернул `accepted=True`;
9. переразбор исходного XML даёт ту же запись;
10. `tier2.validate_event` проходит по бюджету свежести.

Нормативный отказ: `stale`, `no_target_asset`, `no_material_event`, `scheduled`,
`testnet`, `peripheral_only`, `impact_unconfirmed`, `editorial_noise`,
`multiple_assets`, `scope_conflict`, `raw_digest_mismatch`, `market_witness_conflict`.

---

## 7. Что уже проверено (и чем именно)

| проверка | результат | тип |
|---|---|---|
| `unittest discover -p "test_v2*.py"` | 636 тестов, OK (skipped=3) | локальные unit-тесты с mock/Temp DB |
| `unittest discover -p "test_v24*.py"` | 101 тест, OK | то же |
| `series100/tests/test_series_guard.py` | 6 тестов, OK | то же |
| E2E 3 символа (`test_v26_e2e.py`) | 3/3 ok | фейковый executor + фейковый Binance |
| `write_witness` идемпотентность | `recorded` → `already_recorded`; изменение → `market_witness_conflict` | temp DB |
| RSS canary, один цикл | 14/14 HTTP 200, 937 записей, 6.45 с | **реальная сеть, реальные RSS** |
| user-таймер | 6 тиков, 7–17 с, пересечений нет | реальный systemd |
| arrival probe, две SQLite | 4 новых публикации, все ≤120 с, eligible 0 | реальные данные canary |

**Полный production-прогон с моделью НЕ выполнялся.** Ни `--execute`, ни активация
серии, ни реальный вызов DeepSeek в рамках v2.6 не запускались ни разу.

---

## 8. Что проверено НЕТ (и не должно считаться проверенным)

- Полный цикл «событие → 3 ответа модели → seal → +4 ч label» на живых данных.
- Поведение при обрыве между записью биндинга и вызовом модели в реальном процессе.
- Устойчивость при 429/403 на 14 фидах в течение суток (наблюдалось только во
  время коротких прогонов; ошибок не было).
- Корректность выхода `series_runner` при конкурентных `--execute`.
- Соответствие реального `prompts_sha256` замороженному при загрузке свежими
  версиями Python.
- Поведение label-пути на реальных Binance-свечах: только на синтетике.

---

## 9. Вопросы для независимого аудитора

### Утечка данных / lookahead
1. `v26_feed_pipeline.candidate()` переразбирает XML и сверяет запись по
   `title/pub/observed`. Достаточно ли этого, чтобы исключить подмену `published_ms`
   издателем задним числом?
2. `cutoff_ms = decision_cutoff(now_ms)` — округление вниз до часа. Может ли рынок
   после cutoff оказаться доступен до фактического решения?
3. `P.market_features(candles, cutoff, require_cutoff_on_hour=True)` — проверяет ли
   реально, что все 26 часов закрыты **до** cutoff? См. `release/source/v22_live/v21_paper_prep.py`.

### first_seen vs published
4. `first_observed_ms` пишется в момент парсинга. При сбое между записью
   `v24_events` и `rss_metadata` возможно ли, что событие получит более поздний
   `first_observed` при повторе? В `candidate()` есть сверка, но не атомарность
   между двумя БД.
5. `rss_noise_filter` считает окно от `published`, а `tier2` — тоже от `published`.
   Правильно ли, что «свежесть» не учитывает время первого наблюдения?

### Labels / quotes / arms
6. `tier2.validate_quote` требует `observed > last_arm_ms`. Достаточно ли этого
   для исключения lookahead котировки?
7. `exit_candle` возвращает первую свечу, закрывшуюся после `due`. Может ли
   `close >= now_ms` пропустить корректную свечу?
8. `v22_arms.audit_market_arm_isolation` проверяет только header до
   `FROZEN_INPUT_JSON:`. Может ли утечка спрятаться внутри payload?

### Provenance
9. `v26_market_provenance` гарантирует связь «URL запроса ↔ полученные байты», но
   **не** подтверждает, что Binance вернул данные именно по этому тикеру (в klines
   нет поля `symbol`). Достаточна ли такая модель для аудита?
10. `exit_candle` в `v26_symbol_market` проставляет `symbol` из аргумента и не
    сверяет его с payload — `validate_candle(candle, due, symbol=symbol)`
    сравнивает аргумент с самим собой. Насколько это критично?

### Изоляция и upstream
11. `model_executor` собирает upstream-тело из клиентского маппинга
    (`upstream_capture="reconstructed_from_client_mapping"`). Что это оставляет
    непроверенным?
12. `tier2_raw_store` пишет `request/response` в `attestation.jsonl`. Защищён ли
    этот файл от конкурентной записи между руками?

### Арм M и промпты
13. `A.build_prompts_3` строит M из отдельного словаря, не фильтруя `base`. Есть ли
    путь, где поле из `event` может просочиться в M через `market`?

### Идемпотентность и гонки
14. `write_witness` сравнивает `(symbol, request_url, raw_sha256, result_sha256)`.
    Согласованность манифеста и `.bin` проверяется, но вставка идёт после проверки
    файлов. Что произойдёт при двух параллельных `settle_labels`?
15. `series_guard.reserve_model_call` — есть ли гарантия, что повторный вызов не
    спишет второй слот при обрыве между резервированием и записью?

### Двойной вызов
16. `execute_one` перепроверяет `approved()` перед каждой рукой. Достаточно ли
    этого при смене активации между руками?

### Смещения
17. `candidate()` сканирует `LIMIT 256` по `published_ms DESC`. Смещает ли это
    выборку в сторону более поздних публикаций?
18. Порядок армов жёсткий. Есть ли смещение в сравнении `news` vs `market` из-за
    порядка вызовов?

### Тихая потеря данных
19. `candidate()` на любом несовпадении делает `continue`, не логируя причину
    явно. Согласны ли вы, что это приводит к невидимой потере событий?
20. `ingest_one` не откатывает запись в `v24`, если `rss_sidecar.upsert` упадёт.
    Как это восстанавливается?

### Надёжность 14 фидов
21. Наблюдался один HTTP 429 у cryptoslate при повторных запросах подряд. Каков
    минимальный безопасный интервал на источник?
22. `v24_adapters.RateLimiter` держит последний вызов в памяти процесса. Что
    произойдёт при параллельной работе `--poll` из таймера и ручного запуска?

### Бюджет 120 с
23. Протокол фиксирует «effective crypto = 120 s». Наблюдения canary дали 4 новых
    публикации с лагом 67–98 с. Достаточен ли такой выборки, чтобы оценить
    вероятность попадания в бюджет?

### Отсутствие eligible-событий
24. За всё наблюдение `potentially_eligible_at_capture = 0`. Это свойство
    фильтра, свойство фидов или свойство дня? Как это проверить сетевым
    аудитом без активации модели?

### Воспроизводимость развёрнутого SHA
25. `sha256sum` установленной копии совпадает с коммитом. Но `DEPLOYED_SHA` — это
    просто текстовый файл, записанный при развёртывании. Считаете ли вы такую
    фиксацию достаточной?

---

## 10. Ограничения, заявленные явно

- **Все model calls = 0.** Ни один тест не вызывает DeepSeek.
- **Никаких сделок.** Система физически не содержит торгового API.
- **Рынок подтверждён слабо.** Binance не подписывает ответы; связь тикера держится
  на URL запроса и локальном хеше байтов.
- **`observational_tier2`, `upstream_tool_isolation = unverified`.** Эти метки не
  должны читаться как «изоляция подтверждена».
- **Единичное prospective-измерение лага.** 4 публикации — недостаточно для
  статистических выводов.

---

## 11. Воспроизводимость

```bash
# исходный код
git clone -b audit/news-v26-rss14-multiasset-20261120 https://github.com/S960b/S960b.git
cd S960b/research/news_phase4_v25

# компиляция
python3 -m compileall -q release/source series100 rss_expansion

# все unit-тесты
export PYTHONPATH="release/source:release/source/v22_live:series100:rss_expansion"
cd release && python3 -m unittest discover -s tests -p "test_v2*.py"
python3 -m unittest discover -s tests -p "test_v24*.py"
cd ../series100 && python3 -m unittest discover -s tests

# selftest модулей
python3 release/source/v22_live/v26_symbol_market.py
python3 release/source/v22_live/v26_market_provenance.py
python3 release/source/v22_live/v26_rss_collector.py
python3 rss_expansion/rss_noise_filter.py
python3 rss_expansion/rss_sidecar.py
python3 series100/v26_asset_binding.py
python3 series100/v26_feed_pipeline.py
python3 series100/v26_arrival_candidate_probe.py --selftest
python3 series100/v26_series_runner.py --selftest
python3 series100/v26_monitor.py --selftest
```

Транзитивные локальные импорты (все внутри пакета):
`v26_*` → `tier2`, `tier2_integrity`, `tier2_orchestrator`, `tier2_trigger`,
`tier2_label_watch`, `tier2_raw_store`, `tier2_forward_driver`, `v21_paper_prep`,
`v22_arms`, `v22_attestation`, `v22_toolfree_client`, `v24_collector`,
`v24_adapters`, `v24_sources`, `series_guard`, `rss_sidecar`, `rss14_adapter`,
`rss_noise_filter`. Внешних зависимостей, кроме стандартной библиотеки, нет.

---

## 12. Что исключено из пакета

- Живые SQLite базы серии и canary (`rss.sqlite`, `rss_meta.sqlite`, `series.sqlite`).
- `attestation.jsonl`, `frozen-*.json`, `TIER2_V26_ACTIVATION.json`, `V26_TRIGGER_ENABLED`.
- Экспорты пилота от 16.11 с полями `session_id` и `gateway_usage`.
- `.env`, ключи, токены, приватные пути хоста.

Всё, что осталось в `release/data/`, — пустой каталог; фикстуры для ручных
экспериментов лежат в `audit_fixtures/` и помечены как синтетические.

---

## 13. Доступ

Репозиторий `S960b/S960b` **приватный**. Внешнему аудитору потребуется доступ,
который владелец выдаёт самостоятельно; пакет не раскрывает никаких доступов.

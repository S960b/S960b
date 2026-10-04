# REPORT_3 — исправления P0/P1 ревью adfe711

## Итог и границы

Исправления выполнены в `/home/kali/hermes-chat-bridge` относительно предоставленного
baseline `adfe711c91cd35a1c1d5c4ca4ce572a52f8f19ef`. Commit/push не выполнялись.
Этот рабочий каталог не является Git checkout: `git diff` завершился exit 129.
Поэтому фактический revision процесса здесь **unknown**, а не выдуманный baseline hash.
При запуске из чистого Git checkout приложение фиксирует настоящий `git rev-parse HEAD`
при создании процесса/app; вне Git или при dirty checkout сообщает `unknown`.
Версия процесса/SDK metadata: `0.2.0`; `/ping` публикует `version`, `git_revision`,
`process_mode=single-process-pilot` без секретов/сообщений.

Рабочая/старая SQLite БД **не открывалась, не читалась, не изменялась**. Backup сейчас
не выполнялся. Сервер/туннель не запускались и не останавливались. Реальные callback
requests не отправлялись; SafeTrade, конфигурация и другие профили не изменялись.

## Фактические проверки

Все команды использовали существующий `/tmp/bridge-clean-venv/bin/python` и
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. До первого RED полностью прочитан и сохранён
fail-closed `tests/conftest.py`: SQLite только в tmp_path текущего теста (исходное
исключение `:memory:` сохранено), реальные socket.connect запрещены. Затем guard
расширен для read-only SQLite file URI и socket.connect_ex. В RED unsafe-path тестах
guard остановил попытки открыть не-временные пути **до sqlite.connect**.

| Проверка | Фактический результат | Exit |
|---|---|---:|
| Supplied `test_review_adfe711.py`, неизменённая production-версия (RED) | 13 failed, 4 passed, 0 errors, 1.12 s | 1 |
| Новые pilot/generation/revoke регрессии до реализации (RED) | 9 failed, 0 errors, 0.90 s | 1 |
| SDK version regression до исправления metadata (RED) | 1 failed, 1 passed, 0 errors, 0.78 s | 1 |
| Промежуточный development subset после P0/P1 | 51 passed, 3 xfailed, 0 errors, 1.52 s | 0 |
| **Один финальный полный unit/ASGI прогон `pytest tests -q --tb=short -ra`** | **70 passed, 3 xfailed, 0 failed, 0 errors, 1.87 s** | **0** |
| `python -m pip check` | No broken requirements found | 0 |

Финальный набор: 73 collected cases, включая исходные 45, предоставленные 17 и
11 дополнительных проверок. Это **не** «все тесты passed»: три строгих known-limitation
xfail остаются видимыми. Финальная JUnit запись: `reports/adfe711-final.xml`, полный
вывод: `reports/adfe711-final.txt`; pip check: `reports/adfe711-pip-check.txt`.
После этого полного прогона код не менялся; повторных suite/ad-hoc прогонов нет.

Isolated HTTP runner этой доработкой **не запускался**: независимый запуск выполняет
родительский acceptance-процесс в отдельном временном HOME/новой БД. Исторические
12 HTTP smoke baseline не являются результатом текущей версии. Runner обновлён,
чтобы явно передавать новый `BRIDGE_DB_PATH`; e2e assertion теперь требует именно
верхнеуровневый JSON-RPC error, без `result`.

## Что исправлено

### P0.1 — SDK revoke

- `AuthSettings.revocation_options=RevocationOptions(enabled=True)` создаёт настоящий
  `/revoke` и публикует revocation endpoint в metadata.
- Provider реализует `revoke_token(AccessToken | RefreshToken)` и извлекает `token.token`.
  Старые прямые string calls в tests/helpers заменены моделями SDK без ослабления
  проверок. Проверяется client_id модели; чужой HTTP client не отзывает token.
- ASGI SDK проверки покрывают оба типа token, повторный отзыв 200, wrong-client 200
  без отзыва чужого доступа, metadata, недопуск отозванного MCP access, остановку
  подписок/outbox. Это не проверка отключения через UI ChatGPT.

### P0.2 — только новая явная БД пилота

- Default live DB отсутствует: сервис требует явный абсолютный `BRIDGE_DB_PATH`.
  `..`, неканонические пути, symlink и не-regular file отвергаются.
- Fresh Store получает `PRAGMA user_version=3`. Service startup read-only проверяет
  существующий путь и **отвергает unversioned/unsupported DB до schema mutations**;
  повторный запуск той же свежей пилотной БД допускается. Проверка старого tmp DB
  подтверждает неизменность точных байтов при отказе.
- Per-DB kernel `flock` на `<DB>.pilot.lock` удерживается от создания service app
  до завершения lifespan. Второй service app/process не допускается независимо от
  порта; ошибочный lock acquisition не снимает первый lock. Файл lock нельзя удалять
  или заменять при работающем сервере. Это single-process pilot, не multi-process OAuth.
- Прямые Store/BridgeApp остаются внутренними API для временных unit fixtures;
  legacy fixture продолжает демонстрировать отсутствие migration. Они не заменяют
  защищённый service entry point. Legacy upgrade **не заявляется**.

### P1 — lifecycle, ошибки и транспорт

- Поколение операции и pending_generation обеспечивают conditional activation.
  Unsubscribe/revoke увеличивают generation и очищают pending: поздний challenge
  не оживляет отмену и не перезаписывает более новую смену секрета. Нет mutex через
  сетевой await; owner/token проверяется повторно после challenge. Failed rotation
  сохраняет рабочие secret/url/expiry/active. Проверены две reverse-completion гонки:
  два initial challenge и две ротации уже рабочей подписки.
- Callback/invalid params поднимают штатный SDK `MCPError` (-32015/-32602), а не
  возвращают error внутри успешного result. Старые прямые tests теперь проверяют
  поднятую SDK ошибку, сохраняя pending/inactive, повтор challenge и отсутствие bypass.
- Delivery result обновляет только `WHERE done=0`. Overlap пропускается немедленно
  через nonblocking lock; stale 503 не открывает delivered/access_revoked строку.
  Supplied stale-worker barrier адаптирован: второй loop обязан вернуться быстро,
  terminal success фиксируется отдельно, затем проверяется невозможность stale reopen.
- `_post_pinned` явно выполняет TCP/TLS connect, затем проверяет active/expiry/
  generation непосредственно перед request. Fake transports адаптированы под connect;
  revoke во время TLS/setup приводит к закрытию без request bytes.
- Удалены мёртвые Cursor/if False из теста queue race; конкурентные assertions сохранены.

## Оставшиеся известные ограничения

Строгие xfail:

1. `test_legacy_active_flag_is_not_proof_of_callback_verification`: legacy migration
   не реализована; old active=True небезопасен как доказательство callback. Для пилота
   сервис требует новую БД; будущий перенос — отдельно разрешённый backup + migration.
2. `test_stale_loaded_oauth_grant_cannot_be_exchanged_twice[code]`.
3. `test_stale_loaded_oauth_grant_cannot_be_exchanged_twice[refresh]`.

Последовательный HTTP replay code/refresh отклоняется, но атомарного provider consume
раньше загруженного grant и multi-process OAuth guarantees нет. Multi-process outbox
leases/fencing не реализованы; только один сервер/один delivery loop на пилотную БД.
Не заявляется exactly-once HTTP. Нельзя отменить уже записанные сетевые bytes.
CLI job и event — отдельные commit: crash gap остаётся. Нет old/new совместного
подписания при ротации и общего hard deadline DNS+TCP+TLS. Многопользовательской
изоляции нет; первый пилот только синтетический.

`chatgpt_connected`, `subscription_verified`, `same_chat_verified`,
`roundtrip_verified` = **unverified**. Реальный нужный Work-чат, UI OAuth,
`BRIDGE_OK:<job_id>`, повтор события и отмена через UI не проверены. Локальный 2xx
webhook не является ответом чата. Публичное включение/замена старого процесса требуют
отдельного разрешённого действия и независимой приёмки; этот отчёт их не утверждает.

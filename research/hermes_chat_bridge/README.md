# Hermes Chat Bridge — MCP-мост Hermes ↔ ChatGPT

Локальная однопользовательская очередь, OAuth (PKCE/DCR), MCP protocol
`2026-07-28` и webhook-события. Исправления ревью `3b082fe` и фактические
результаты описаны в `REPORT_3.md`; `REPORT_1.md` / `REPORT_2.md` — исторические отчёты.
Текущая доработка: P0/P1 ревью `adfe711`, только отдельный синтетический пилот.

**Подключение к ChatGPT не подтверждено:** `chatgpt_connected`,
`subscription_verified`, `same_chat_verified`, `roundtrip_verified` = **unverified**.
Действующий сервер не перезапускался и продолжает исполнять прежний код.

## Воспроизводимая установка

Проверено на Linux / Python 3.13.3. `requirements.lock` фиксирует все
транзитивные зависимости и тестовое окружение; `requirements.txt` включает lock.
Это version lock, не hash lock; установка требует доступности этих версий в индексе.

```sh
cd ~/S960b/research/hermes_chat_bridge
python3 -m venv .venv-clean
.venv-clean/bin/python -m pip install -r requirements.txt
.venv-clean/bin/python -m pip check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-clean/bin/python -m pytest tests -q --tb=short
```

Тесты работают с временными SQLite и синтетическими токенами/ASGI;
callbacks, DNS, TCP и TLS замоканы. Autouse-ограничитель запрещает открывать
SQLite вне каталога текущего теста и устанавливать реальные socket connections.
`e2e_test.py` обновлён под внутренний login state и обязательный `Mcp-Name`.
Он требует явно заданные loopback URL и тестовый пароль. Против действующего
сервера его не запускать. Безопасный runner создаёт временный HOME/SQLite,
случайный пароль и отдельный порт, проверяет readiness и останавливает свой процесс:

```sh
.venv-clean/bin/python scripts/run_isolated_http_e2e.py
.venv-clean/bin/python scripts/verify_pilot_startup.py
```

Итоговая приёмка: 73 passed, 3 strict xfailed; 12 HTTP-проверок и 5 проверок
реальных локальных процессов. Полные результаты и RED — в REPORT_3.md и reports/.
После commit из чистого checkout можно проверить точный build процесса:
`.venv-clean/bin/python scripts/verify_pilot_startup.py "$PWD" "$(git rev-parse HEAD)"`.
Вне Git или при dirty checkout revision честно будет `unknown`.

Этот HTTP-прогон не является подключением настоящего UI ChatGPT.

## Запуск — только отдельным разрешённым действием

Следующая команда — инструкция для будущего запуска, не выполненный шаг:

```sh
BRIDGE_DB_PATH='/absolute/path/to/NEW-pilot.db' BRIDGE_BASE='http://127.0.0.1:8765' BRIDGE_USER='owner' BRIDGE_PASS='<сильный пароль владельца>' .venv-clean/bin/python bridge_server.py
```

При пустом/отсутствующем `BRIDGE_PASS` приложение отказывает **до открытия БД**.
Debug выключен. Пароль не генерируется автоматически и не пишется в репозиторий.
Обязателен явный абсолютный `BRIDGE_DB_PATH`, без `..` и symlink; default live DB отсутствует.
Выберите **новый** путь только для синтетических задач. Service startup отвергает существующую
БД без версии пилота (`PRAGMA user_version=3`); свежесозданный пилот можно повторно открыть.
Legacy migration не реализована: old active=True не является доказательством challenge.
Старую БД нельзя обновлять этим запуском. Она не читалась, не изменялась, backup сейчас не делался.
Перед будущим переносом данных нужен отдельно разрешённый согласованный SQLite backup и миграция.
Один процесс сервера на БД принудительно обеспечен kernel `flock` на `<DB>.pilot.lock`,
удерживаемым от factory до завершения lifespan. Не удаляйте и не заменяйте lock-файл во время работы.
CLI должна получать тот же явно выбранный путь **после инициализации БД сервером**;
не используйте CLI для создания пилотной БД или legacy upgrade. Выбирайте DB вне Git checkout.
`Store`/`BridgeApp` напрямую — внутренние API для unit fixtures, не service entry point.
Публичный туннель не запускался; инструкции подключения UI требуют отдельной проверки.

## Контракт и безопасность

- Все tools/events требуют подтверждённого SDK `get_access_token()` с subject
  настроенного владельца, scope `bridge` и правильным resource. Default principal нет.
  Очередь не имеет owner: **многопользовательской изоляции нет**.
- Tools объявляют OAuth `bridge` в `_meta.securitySchemes` и явные
  read/write/idempotent аннотации SDK. Интерпретация метаданных UI не проверена.
- `/login` принимает только существующее внутреннее случайное состояние; OAuth
  client state хранится отдельно и возвращается клиенту. HTML атрибуты экранируются.
  TTL состояния 600 секунд, максимум 5 попыток, атомарное одноразовое потребление.
  Дополнительно общий persistent SQLite budget владельца: 20 попыток за 600 секунд,
  включая успешные попытки. Новый state не сбрасывает бюджет; превышение даёт 429.
  Это throttle, не защита от распределённого account-lockout DoS.
- SDK `/revoke` включён и объявлен в OAuth metadata; принимает access/refresh модели,
  повторный HTTP отзыв идемпотентен. Неверный client не отзывает чужой token; связанные
  токены этого client/subject удаляются, подписки владельца консервативно отменяются.
  Это локально проверено через ASGI SDK, **не** UI-отключение ChatGPT.
- Ошибки callback/invalid params — top-level JSON-RPC error через SDK `MCPError`,
  а не успешный result с вложенным error.
- Подписка pending/inactive до успешного challenge. Неуспешная смена секрета
  сохраняет рабочие параметры. Conditional generation/pending_generation fencing не позволяет
  позднему challenge отменить unsubscribe или перезаписать более новую смену секрета.
  Unsubscribe увеличивает generation; после challenge повторно проверяется owner/token. `ttlMs` должен быть положительным, не превышать
  серверный максимум (по умолчанию 7 дней); `refreshBefore` — timezone-aware ISO8601.
  Аргументы строго `{"queue":"test"}`, дополнительные поля отклоняются.
- Callback: HTTPS без credentials/fragment, все DNS-адреса публичные. TCP соединяется
  с **проверенным literal IP** без повторного DNS; TLS проверяет исходный hostname/SNI
  через default SSL context. Environment proxies не используются, редиректы не следуются.
  Работа/DNS вынесены в максимум 4 workers, очередь не растёт, socket timeout ≤10 секунд,
  response ≤256KiB. DNS системного resolver не имеет отдельного hard deadline, но число
  занятых workers ограничено; отмена async-задачи не освобождает занятую worker slot раньше времени.
- Standard Webhooks подписывает `webhook-id.timestamp.raw_body`. Новый ID на каждый
  challenge. Outbox фиксирует body/eventId/timestamp при постановке; повторы меняют
  только заголовок времени подписи. Уникальность `(sub_id,event_id)` и стабильный ID
  от queue/job предотвращают повторную постановку одного триггера.
- Бюджет доставки: 8 попыток / 24 часа; причины завершения сохранены. 2xx = доставка,
  не ответ чата. Перекрывающий `deliver_pending` немедленно пропускается (nonblocking lock),
  терминальная строка не перезаписывается (`UPDATE ... WHERE done=0`). После явного TCP/TLS
  connect, непосредственно перед request, повторно проверяются active/expiry/generation; отзыв токена
  консервативно прекращает все подписки данного владельца. Уже отправленный запрос
  нельзя отозвать; exactly-once HTTP доставка не заявляется.
- Ответ записывается SQLite compare-and-swap `WHERE reply IS NULL` с rowcount;
  разные соединения не перезаписывают первый ответ. Идентичный ответ идемпотентен,
  другой — явный конфликт.
- Приложенный `discover_events_compat_poc.py` установлен непосредственно в factory.
  Он дополняет только успешный `server/discover` протокола `2026-07-28` **после**
  сериализации SDK. Auth/validation/transport не обходятся. Зависимость от
  `_lowlevel_server` требует wire-regressions при обновлении mcp/mcp-types (сейчас 2.3.0).
- Transport Host allowlist содержит точный hostname:port из BRIDGE_BASE, без
  отключения DNS-rebinding protection. Другой порт не разрешается.

## Файлы

`bridge_server.py`, `bridge_queue.py`, `bridge_cli.py`, supplied middleware,
`tests/`, `requirements.txt`, `requirements.lock`, `REPORT_3.md`.
Локальная CLI: `put`, `get`, `list`, `subs`, `verify`. List/subs печатают все строки,
включая корректный пустой результат. Не запускалась против действующей БД повторно.
Секреты подписок находятся в SQLite (0600), исключите `data/` из публикации.


## Известные ограничения пилота

Три `xfail(strict=True)` остаются видимыми: одна legacy migration и два stale-loaded
OAuth grant (code/refresh). Последовательный HTTP replay отклоняется, но provider
не гарантирует атомарное consume объекта grant между несколькими процессами/соединениями.
Multi-process delivery leases не реализованы; запуск только одного серверного процесса.
CLI job/outbox — два commit, возможен промежуточный сбой; transactional outbox/recovery
не обещается. Совместная подпись old/new key при ротации не реализована. DNS и socket
 timeout не дают общего hard deadline. Already-written HTTP bytes не отзываются.
UI, нужный Work-чат, реальный callback и ответ `BRIDGE_OK:<job_id>` остаются unverified.

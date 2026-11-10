# REPORT_2 — доработка по review_3b082fe

## Результат

Исправлены шесть обязательных и шесть дополнительных пунктов ревью в рабочем
каталоге `/home/kali/hermes-chat-bridge`, затем выполнена независимая приёмка и
добавлены уточняющие исправления. Финальный набор: **45 passed in 1.78s** в
чистом окружении по lock; отдельный isolated loopback HTTP smoke: **12 passed, 0 failed**.
Реальное подключение ChatGPT **не выполнялось и не подтверждено**.

| Флаг | Значение |
|---|---|
| chatgpt_connected | unverified |
| subscription_verified | unverified |
| same_chat_verified | unverified |
| roundtrip_verified | unverified |

Имеющийся сервер не перезапускался, его PID не трогали: исправленный код на диске
не означает исправление уже работающего процесса. Родительский агент остановил
публичный туннель до ремонта; он не возобновлялся. SafeTrade и конфигурация/модель
Hermes не изменялись. Рабочий каталог не является git repository; проверяемые
материалы синхронизируются в `S960b/S960b/research/hermes_chat_bridge` для публикации.

## Исправления

1. **Подпись**: `_sign_body(secret, webhook_id, timestamp, raw_body)` подписывает
   точные bytes `id.timestamp.body`; независимый HMAC verifier проверяет challenge
   и доставку. ID challenge новый при каждом запросе. CLI сравнивает полный HMAC,
   а не префикс `v1,`.
2. **Подписки**: новые параметры pending/inactive; active только после успешного
   challenge. Неуспешные повторы повторно проходят проверку. Смена секрета
   требует challenge; при отказе рабочая подписка сохраняется. Callback входит
   в идентичность подписки, новый callback проходит отдельную проверку.
3. **Login**: экранирование атрибутов (включая quotes); GET принимает только
   существующее непросроченное внутреннее состояние. Внутренний случайный ID
   отделён от OAuth client state. TTL 600 секунд, 5 попыток, атомарные SQLite
   DELETE/UPDATE RETURNING исключают двойное потребление между соединениями.
4. **SDK**: приложенный `discover_events_compat_poc.py` интегрирован без изменения
   его содержимого, вызовом `install_discover_events_compat(mcp)` в factory.
   Сериализация/auth/validation SDK остаются штатными. Тестовая PoC fixture
   больше не устанавливает middleware повторно. mcp/mcp-types закреплены 2.3.0.
5. **Events/outbox**: timezone-aware ISO `refreshBefore`, учёт `ttlMs`, проверка
   срока/active перед отправкой и после DNS; фиксированные body/eventId/timestamp,
   стабильный event ID по queue/job, уникальная постановка по `(sub_id,event_id)`.
   Максимум 8 попыток/24 часа, backoff ограничен; terminal reason сохранена.
   Отзыв токена прекращает подписки владельца и queued deliveries; нельзя
   отменить запрос, уже ушедший в сеть. Exactly-once транспорт не заявляется.
6. **Ответы**: SQLite CAS `UPDATE ... WHERE reply IS NULL`, rowcount;
   первый ответ остаётся, идентичный идемпотентен, другой явно конфликтует.
7. **Callback**: запрещены private/loopback DNS/IP, URL credentials и fragment,
   redirects не следуются. Проверенный IP используется напрямую socket.connect,
   без повторного DNS; TLS default context проверяет оригинальный hostname/SNI.
   http.client не использует environment proxies. Проверки детерминированно
   подменяют DNS/TCP/TLS, реальная TLS-сессия не проверялась.
8. **Неблокирующая обработка**: DNS/HTTP callbacks работают в bounded pool
   максимум 4 workers без неограниченной очереди; socket timeout ≤10 секунд,
   response ≤256KiB. Slot не освобождается при отмене async-запроса до окончания
   worker. Системный DNS resolver не имеет отдельного hard deadline, но число
   зависших работников ограничено; остальные ASGI запросы не блокируются.
9. **Principal**: реальный SDK auth context `get_access_token`, owner subject,
   scope, resource, expiry, существование токена; нет fallback owner. Все
   tools/events защищены. Provider отклоняет foreign subject; HTTP tests это
   проверяют. Это single-owner bridge, не многопользовательская изоляция.
10. **Tool metadata**: SDK read/write/idempotent annotations и явные OAuth
    `_meta.securitySchemes` со scope `bridge`. UI-интерпретация не проверялась.
11. **CLI**: list/subs выводят все строки и корректно завершаются при пустой
    выдаче. Удалён динамический повторный импорт сервера, обходивший test patch.
12. **Запуск/dependencies**: debug выключен, отсутствие BRIDGE_PASS вызывает
    отказ до открытия БД. Полный pinned version lock и установка в чистом venv
    проверены; README обновлён. Lock не содержит artifact hashes.

## Реальные команды и результаты

Сначала supplied tests и middleware скопированы из архива. Производственный код
не менялся до первого RED. В existing venv установлен pytest 9.1.1.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/bin/python -m pytest tests/test_review_3b082fe.py -q --tb=short
```

Исходный код: **12 failed, 4 passed in 0.88s**, pytest exit 1.
Полный вывод сохранён в `review-red.txt` (wrapper shell тогда завершился 0 после
вывода файла; это не exit code самого pytest).

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/bin/python -m pytest tests/test_discover_compat_poc.py -q --tb=short
```

Исходный factory с supplied PoC fixture: **4 passed in 0.71s**, exit 0.

Добавленные инварианты сначала воспроизвели ошибки: `added-red.txt` — 10 failed;
последующие targeted RED: `edges-red.txt` — 2 failed, 5 passed;
`guard-red.txt` — 1 failed. Некоторые дополнительные проверки уже были зелёными,
поскольку проверяли ранее исправленный путь, а не новую реализацию.

Прогон первого этапа после правок и fail-closed test guard (до независимой приёмки):

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 venv/bin/python -m pytest tests -q --tb=short --junitxml=review-final.xml
```

```text
......................................                                   [100%]
38 passed in 1.20s
```

Exit 0; полный stdout `review-green.txt`, JUnit `review-final.xml`.
Это 16 supplied regressions + 4 supplied compatibility + 18 дополнительных
проверок. Assertions исходных регрессий не ослаблены. Для race barrier перенесён
перед условным UPDATE, поскольку SELECT больше не предшествует записи.
Для прямых calls subscription fixture устанавливает настоящий SDK
AuthenticatedUser/AccessToken context (с синтетическим сохранённым токеном).

Чистое окружение создано в `/tmp/bridge-review-venv.zi8e74`:

```sh
python3 -m venv /tmp/bridge-review-venv.zi8e74
/tmp/bridge-review-venv.zi8e74/bin/python -m pip install -r requirements.lock
/tmp/bridge-review-venv.zi8e74/bin/python -m pip check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/bridge-review-venv.zi8e74/bin/python -m pytest tests -q --tb=short
```

Install exit 0, `No broken requirements found.`; итог **38 passed in 1.20s**, exit 0.
`clean-install.txt`, `clean-tests.txt` содержат фактические выводы.
Python 3.13.3, mcp 2.3.0, mcp-types 2.3.0; все версии в `requirements.lock`.
Дополнительно existing `pip check`: `No broken requirements found.`;
compileall production modules + tests: exit 0.

## Отклонения изоляции — важно

Не могу утверждать, что *все промежуточные* прогоны соответствовали запрету
live DB/network: до внедрения общего guard произошли две ошибки тестовой изоляции.

- Первый добавленный RED-тест CLI вызвал прежний dynamic import, создавший
  новый модуль bridge_server без monkeypatch DB_PATH. Команда list открыла
  стандартную `/home/kali/hermes-chat-bridge/data/bridge.db` через Queue,
  выполнила `CREATE TABLE IF NOT EXISTS` и SELECT. Тест упал на количестве
  строк. Команд записи сообщений, Store migration, ответа или изменения
  подписок в этом пути не выполнялось. После обнаружения live DB повторно
  не открывали для проверки; её неизменность побайтово **не подтверждена**.
- Первый RED-тест транспорта на старом urllib пути сделал реальную попытку
  TCP к синтетическому proxy `127.0.0.1:1`, завершившуюся ConnectionRefusedError.
  Успешного callback/передачи payload не было. Сетевой guard добавлен сразу
  после обнаружения; RED повторён с запрещённым socket.connect.

CLI импорт исправлен; `tests/conftest.py` теперь autouse запрещает sqlite.connect
вне tmp_path текущего теста и любые реальные socket.connect. **Последние 38/38
в обоих окружениях полностью проходят с этим guard**. Это уточнение обязательно
передать владельцу, а не выдавать всю историю за безусловно изолированную.

## Ограничения и следующий этап

Production DB migration/резервная копия, публичный HTTPS handshake, ChatGPT UI,
действительная OAuth-сессия ChatGPT, настоящий callback, ответ того же чата,
реальный restart/replay не проверялись. `e2e_test.py` после первого этапа обновлён
и выполнен только через isolated runner с временным HOME/SQLite и отдельным портом.
Middleware зависит от private SDK `_lowlevel_server`; при обновлении SDK нужны
wire tests. Локальные mocked проверки не равнозначны connected.

Следующий этап требует отдельного разрешения: обновление работающего сервера
после backup → UI/OAuth/subscription → одна синтетическая задача → ответ
`BRIDGE_OK:<job_id>` → чтение CLI, затем repeat/restart. Ничего из этого этапа
в текущей доработке не выполнялось.

## Независимая приёмка и финальные уточнения

- Исходный Git `3b082fe` отдельно воспроизведён: 12 failed / 4 passed; supplied
  middleware отдельно 4 passed. Полный baseline в `reports/baseline-pytest.txt`.
- Пять попыток только на state позволяли обойти throttle новым state. Добавлен
  атомарный persistent SQLite budget владельца: 20 попыток за 600 секунд, 429
  после исчерпания. Считаются и успешные попытки; это не защита от account-lockout DoS.
- Аргументы события теперь строго соответствуют `{"queue":"test"}`, без extra fields.
  Для этих двух уточнений новые тесты сначала дали 2 failed, затем прошли.
- Реальный loopback smoke обнаружил 421 Invalid Host на URL с портом. Allowlist
  теперь использует точный netloc BRIDGE_BASE, не отключая rebinding protection;
  регрессия сначала падала, затем разрешённый порт прошёл, чужой остался запрещён.
- `e2e_test.py` использует внутренний state, Mcp-Name и явно заданные loopback URL /
  синтетический пароль. `scripts/run_isolated_http_e2e.py` создаёт отдельный процесс,
  HOME/SQLite/порт, проверяет readiness, после теста завершает именно свой процесс.
- Добавлена полноценная ASGI проверка DCR → OAuth PKCE → token → discover → чтение
  синтетической задачи → запись BRIDGE_OK:<job_id> → чтение того же ответа. Это
  локальная интеграция, не реальный ChatGPT чат и не UI OAuth.
- Независимые проверки literal-IP TCP, SNI исходного hostname, закрытия сокета при
  TLS-ошибке и одноразового consume между соединениями также сохранены.

Финальные команды (из рабочего каталога):

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/bridge-clean-venv/bin/python -m pytest tests -q --tb=short --junitxml=reports/final-pytest.xml
/tmp/bridge-clean-venv/bin/python scripts/run_isolated_http_e2e.py
/tmp/bridge-clean-venv/bin/python -m pip check
```

Фактические результаты: **45 passed in 1.78s**, pytest exit 0; isolated HTTP
**12 passed / 0 failed**, exit 0; `No broken requirements found.`
Stdout и JUnit сохранены в `reports/final-pytest.txt`, `reports/final-pytest.xml`,
`reports/isolated-http-e2e.txt`. В новом `/tmp/bridge-clean-venv` выполнена установка
полного lock; установленный lock совпадает с проектным `requirements.lock`.

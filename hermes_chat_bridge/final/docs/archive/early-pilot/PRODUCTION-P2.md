# P2a: изолированный opt-in wakeup receiver (пока offline)

Первый код адаптера под фактически поступающий `hermes.message.created`.
Это не установка/подключение действующего моста, не новый writer и не замена P1.
P1 service.py и его CLI **не изменены**: установленный 8789 продолжает работать
как раньше. Новый модуль сам не слушает сокеты и не создаёт потоки/БД/подписки.

## Граница и состав

- `WakeupApplication(panel, gate=None)` — WSGI-обёртка над существующим P1.
  `/bridge/wakeup` выключен (404), пока trusted host явно не передал gate.
  Остальные маршруты остаются в PanelApplication с прежним owner-входом.
- `WakeupGate(key)` требует ровно32байта отдельного ключа trusted config.
  Это НЕ пароль owner, НЕ PAT и НЕ секрет уже работающей подписки. Никакого
  поиска/чтения/выдачи ключей, env, путей credential или файлов в модуле нет.
  Фиксированный ключ в тестах — публичная fixture, **не использовать при запуске**.
- Новый локальный контракт подписи `X-Hermes-Signature: sha256=<hex>` — HMAC-SHA256
  по точным байтам UTF-8 JSON. Это контракт **будущего opt-in forwarder**, а НЕ
  утверждение, что действующий мост уже отправляет такую подпись. Его секреты
  не извлекать и не переиспользовать. Текущие webhook/subscriptions не менять.
- Только loopback peer и exact Host из P1; POST `/bridge/wakeup`, без query,
  Origin, forwarded headers, TE/Expect. Content-Type application/json,
  Content-Length1..4096. WSGI-обёртка не видит дубликаты HTTP headers:
  framing/duplicate rejection и socket timeout обязан обеспечить HTTP host.
- Подпись проверяется до JSON. Затем strict schema, duplicate-key/NaN/deep/
  invalidUTF8/extra fields rejected. Никаких message body/команд/owner полей.
- Верхний объект ровно name/eventId/timestamp/data. Data ровно
  created_at_utc/direction/job_id/queue/status/task_id. EventId `evt_`+32hex,
  job_id ограничен ASCII форматом; name фиксирован. UTC timestamp не старше120s,
  не более30s в будущем, created отличается не более5s.
- Только наша task_3c2d37a2485b/queue=test/to_chatgpt/status=pending;
  иные корректно подписанные события игнорируются **без claim/чтения job**.
- Hash eventId в bounded памяти, TTL180s; повторы не создают запрос. Новое
  событие не чаще30s. Сигналы coalesce в один pending-bit, не очередь заданий.
  Restart забывает replay-cache. Это НЕ durable exactly-once, но эффект только GET.
- HTTP202 `scheduled` означает ТОЛЬКО pending-bit, **не выполненный refresh**.
  `gate.drain(reader)` вызывается trusted host и принимает точный SnapshotReader;
  он выполняет только его фиксированный refresh (ReadOnlyTransport GET-only).
  Ошибка источника остаётся fail-closed; pending не восстанавливается и не
  вызывает скрытый retry. Периодический polling — резервный путь.
- Host должен вызывать periodic refresh и drain в ОДНОМ reader-loop, не
  параллельно, и не создавать потоки/произвольные callbacks по данным события.
  Это связывание намеренно ещё не включено в живой CLI.

## Что действительно проверено

ChatGPT: Python3.12.14/pytest9.1.1, **433 passed**, compileallOK:
394 прежних +39 новых. Новые тесты без HTTP sockets/subprocess, SQLite только
tmp_path; реальные PAT/GitAPI/истории не используются. Проверена цепочка
WSGI envelope → HMAC/schema → pending → точный SnapshotReader → FakeApi GET-only,
а также auth separation, disabled default, scope, freshness, malformed JSON,
dedup/concurrent accept, cooldown/coalescing/restart caveat, fail-closed drain,
method/Host/peer/Origin/body bounds. Это не live HTTP/wakeup/forwarder-приёмка.

## Единственное поручение MT на этот этап

Обычный SSH-fetch exactSHA из сообщения ChatGPT в НОВУЮ чистую кодовую копию,
без reset/force/правок. Отдельное testenv с requirements-service.txt и pytest9.1.1,
при AppImage использовать системный Python и env -u APPDIR -u APPIMAGE.

```sh
git rev-parse HEAD
PYTHONPATH=src python -m pytest tests -q
python -m compileall -q src
git status --short
```

Ожидается433passed. Отчёт SHA/Python/pytest/summary/exit/compileall/status.
НЕ исполнять предыдущие команды init/serve/smoke из других runbooks: они не
поручение этого этапа. Не читать секреты, не вызывать WSGI над живыми данными,
не создавать ключи/БД/сервисы/подписки, не трогать установленный P1 или8787,
мост/OAuth/cron/истории/canaryБД, не запускать worker/close/cancel/delete/create.
MT код не меняет. После фактического отчёта ChatGPT проверяет и готовит отдельное
изолированное wiring/испытание; не объявлять подключение по одним offline-тестам.

Дальше нужны отдельный reviewed host-wiring, безопасный локальный способ
provisioning выделенного ключа и реально разрешённая opt-in точка forwarder.
Изменение защищённого действующего моста/подписок не разрешается этим документом.
Общая authority всех writers и реальное удаление по-прежнему не развёрнуты.

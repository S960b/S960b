# git-dialog.v1 — протокол передачи хода через Git

> **Важно для новой ChatGPT-сессии (06.11.2026):** эта спецификация содержит исторические этапы реализации. Актуальный набор инструментов и безопасная последовательность записи приведены в [CHATGPT_GIT_BRIDGE.md](CHATGPT_GIT_BRIDGE.md). В обнаруженном MCP есть `dialog_index`/`dialog_notice`, но нет `dialog_view`/`dialog_append`. Обязательный preflight до Git POST: `scripts/preflight_chatgpt_append.py` проверяет реальный snapshot и новое состояние через `parse_snapshot`/`prepare_append`. Сам по себе preflight не является серверной branch protection.


Статус: модель протокола, SQLite-журнал и opt-in REST-адаптер с изолированными
контрактными тестами. Живой транспорт на машине MT, панель и автоисполнитель
НЕ включены и НЕ проверены сквозным тестом.

## 1. Беседа и ветка

- Одна беседа = один `task_id`.
- `task_id` строго соответствует `^[a-z0-9][a-z0-9_-]{2,63}$`; ветка: `dialogs/<task_id>`.
- Файлы беседы: `task.json`, `state.json`, `messages/<seq>.<actor>.json`.
- `main` хранит только описание и шаблоны; переписка — только в ветке `dialogs/<task_id>`.
- Открытие беседы — отдельная явная операция. Append НИКОГДА не создаёт отсутствующую ветку.

### task.json

```json
{"protocol":"git-dialog","version":"1","task_id":"task_x","title":"...","goal":"...","scope":[...]}
```

### state.json

```json
{"protocol":"git-dialog","version":"1","task_id":"task_x","revision":3,
 "last_message_id":"m3","seq":3,"next_actor":"mt","status":"working"}
```

- `status` — статус приложения: `waiting|working|error|done|closing`.
- `next_actor` — чей ход: только `chatgpt|mt`.
- `revision` увеличивается на каждый принятый append.

## 2. Сообщение

`messages/<seq>.<actor>.json`, неизменяемый UTF-8 JSON:

```json
{"protocol":"git-dialog","version":"1","task_id":"task_x","message_id":"m2",
 "seq":2,"from":"chatgpt","to":"mt","reply_to":"m1","kind":"task|report|ack|close",
 "created_at_utc":"2026-10-26T00:00:00Z","body":"..."}
```

- `seq` монотонен; порядок определяется `seq`, не `created_at_utc`.
- `reply_to` у первого сообщения `null`, далее — ровно предыдущий `message_id`.
- `body` — только данные. Это НЕ право исполнять команды.
- Лимит тела: 64 KiB (UTF-8). Превышение отвергается.
- Время `created_at_utc` **строго** `YYYY-MM-DDTHH:MM:SSZ`; миллисекунды (например `.562Z`) и `+00:00` запрещены. Генерировать через UTC `strftime` / JS `toISOString().slice(0,19)+"Z"`, проверять до Git.
- Новое состояние и сообщение должны проходить `parse_snapshot`/`prepare_append` *до создания Git-объектов* в ручном fallback; перед сдвигом Git ref отдельно проверяется кандидат.

## 3. Валидация (отвергается)

- чужой `task_id`; неизвестная версия/actor; неверный ход (`from` != `next_actor`);
- неверный `reply_to`/`seq`; конфликт `message_id` с другим содержимым;
- некорректные или опасные пути (`..`, абсолютные, лишние сегменты);
- тело сверх лимита; `from == to`; append после `done`/`closing`.

## 4. prepare_append

`prepare_append(snapshot, expected_head, message, ...) -> Prepared`

- Чистая функция: исходный snapshot не мутируется.
- Формирует сообщение и НОВОЕ состояние как одну предполагаемую транзакцию/commit.
- `commit_parent` = `expected_head` (прочитанный head).
- Повтор `message_id` с идентичным payload — no-op (тот же ID, `created=False`).
- Тот же `message_id` с другим payload — `conflict`.

## 5. Публикация (CAS)

- Запись только если ветка существует и её head равен прочитанному snapshot.
- Новый commit имеет parent = прочитанный head; публикация — fast-forward существующего ref.
- Без force, без неявного создания ветки.
- Два конкурентных append от одного head: один победитель; второй обязан перечитать.
- Удалённая ветка не восстанавливается запоздалым писателем.

Этап 2 реализует `FakeRefStore` с этим контрактом. Проверки fake CAS НЕ являются проверкой реального GitHub API.

## 6. Журнал исполнения

Локальный журнал (вне Git): `execution_received|started|result_ready|published` по `message_id`.

- При сбое после внешнего эффекта до durable result exactly-once не обещается.
- Неидемпотентное действие не повторяется автоматически; результат помечается `unknown` и требует решения.
- Heartbeat-коммит на каждый poll не создаётся. Тайм-аут не даёт права на автоматический повтор.

## 7. Ограничения этапа

Реализовано и протестировано offline: чистые правила, валидатор, prepare_append,
fake CAS, SQLite-журнал и REST-адаптер с HTTP-double, тесты в tmpdir.
НЕ реализовано: панель, автоисполнитель, lifecycle-реестр удаления. Реальные
ветки `dialogs/<task_id>` не создавались, REST-адаптер на живом API не запускался.

## 8. Уточнения по ревью (этап 2)

### Ref и пути

- Git-ref беседы: `dialogs/<task_id>` (`branch_ref(task_id)`).
- Файлы лежат ВНУТРИ этой ветки: `messages/<seq>.<actor>.json`.
- `message_path(task_id, seq, actor)` — старый логический locator для отображения,
  а НЕ путь для Git Contents/Tree API. Он сохранён для совместимости тестов.
- Для записи внутри ветки применять `message_file_path(seq, actor)`, который
  возвращает только `messages/<seq>.<actor>.json`. Имя ref не является каталогом.

### Строгие типы

- `seq` и `revision` — целые неотрицательные; `bool` отвергается
  (в Python `bool` — подкласс `int`, поэтому проверка явная).
- `message_id`/`reply_to` — непустые, `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.
- `task_id` — полный матч `^[a-z0-9][a-z0-9_-]{2,63}$`, управляющие символы запрещены.
- `created_at_utc` — ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`), проверяется и календарно.

### Повтор (delayed redelivery)

- Повтор ранее принятого `message_id` — no-op: `created=False`, исходный
  `Message`, ТЕКУЩИЕ `state`/`head`; новая revision не создаётся.
- Работает и после нескольких ходов, и после `done`/`closing`.
- Без `created_at_utc` — сохраняется исходный timestamp; явно другой — `conflict`.
- Любое иное отличие payload при том же ID — `conflict`.

### Разбор входных данных

- `parse_task`, `parse_state`, `parse_message`, `parse_snapshot` отвергают
  повреждённые/неполные структуры машинными `DialogError`; молча не исправляют.
- `prepare_append` всегда валидирует snapshot, поэтому путь с повреждённым
  snapshot недоступен.

### Воспроизводимость тестов

```
PYTHONPATH=src python -m pytest tests/test_git_dialog.py -q
```

Python 3.13; единственная зависимость — pytest. Тесты работают только в tmpdir
и с fake-снапшотами; сеть и subprocess запрещены autouse-фикстурой.
В исходной ревизии этапа 2: 28 passed. Новые проверки запускаются всей командой
`PYTHONPATH=src python -m pytest tests -q` (Python >=3.12, pytest).

## 9. Локальный журнал исполнения (код ChatGPT)

`git_dialog.journal.ExecutionJournal` — отдельная SQLite-база, НЕ БД моста.
Содержит записи по `(task_id, message_id)`, hash и immutable payload запроса,
run_id, сохранённое сообщение результата и SHA подтверждённой публикации.
Создаваемый файл имеет права 0600; чужая БД с таблицами отвергается без изменения.

Переходы: `received -> started -> result_ready -> published`.
От `started` при подтверждённой остановке его worker возможен только явный
переход `mark_unknown(..., run_id)`; автоматического reset/retry нет.
Открытие журнала само по себе НИЧЕГО не запускает и не меняет started на unknown.

- `receive(message)` принимает только входящий kind=task для заданного actor,
  повтор идентичного payload возвращает текущую запись, другой payload — conflict.
- `start(..., run_id)` возвращает True ровно одному писателю. False НЕ даёт
  разрешения выполнять эффект, включая повтор того же run_id.
- `save_result(..., run_id, result_message)` сохраняет проверенный неизменяемый
  report. Чужой run_id, task/reply_to/seq/actor отвергаются.
- `mark_published(..., run_id, sha)` допустим только после durable result.
  Проверку наличия report в удалённом commit обязан выполнить будущий адаптер;
  сам факт сохранения SHA в журнале не доказывает публикацию на GitHub.
- После сбоя при result_ready берётся уже сохранённый report, а не исполняется
  задача заново. Если внешний эффект мог состояться, но результата нет,
  нужен supervisor/оператор; exactly-once для внешних эффектов не обещается.

В этой ревизии также закрыты обходы прямыми dataclass: строгий bool seq истории,
проверка чередования actors и next_actor, недопустимый контейнер history,
отсутствие title, числовая version, несовместимость close/status и некорректный UTF-8.
Autouse-фикстура всех тестов запрещает сеть, subprocess и SQLite вне tmp_path.

Это библиотека, не реальный исполнитель. Автоматического опроса, панели и
удаления реальной ветки по-прежнему НЕТ. Рабочие сервисы/подписки/БД не менялись.

## 10. Opt-in GitHub REST-адаптер (код ChatGPT)

`git_dialog.github.GitHubDialog(transport, repository)` привязан к одному
репозиторию. Перед чтением/записью проверяет `private=true` и точное full_name.
`JsonHttp(token)` требует ЯВНО переданную уже разрешённую API-авторизацию;
не ищет её в env, истории, SSH, gh, файлах или настройках коннектора. Конструктор
не обращается в сеть. Единственный host — https://api.github.com; redirect,
автоматические повторы и вывод исходных HTTP-ошибок/ответов запрещены.

- `load(task_id)` читает точный ref, затем snapshot по зафиксированному SHA,
  recursive tree и blob по SHA (не по меняющейся ветке). Проверяет полное дерево,
  truncated, regular-file mode, имена сообщений и hash blob; duplicate JSON keys,
  некорректные пути, чужие задачи и повреждённые истории отвергаются.
  Ограничения: 1000 сообщений, 8 MiB JSON истории, 2 MiB на HTTP-ответ/blob,
  10000 записей tree. При превышении — ошибка, НЕ обрезанная история.
- `append(task_id, expected_head, ...)` использует модель протокола. Создаёт
  tree с новым immutable message и state.json, commit с ЕДИНСТВЕННЫМ parent
  expected_head, затем независимо читает candidate и проверяет точные файлы,
  state, parent и сохранность прежних файлов. Только затем — повторный preflight
  и PATCH существующего `/git/refs/heads/dialogs/<task_id>` с `force=false`.
- Адаптер НИКОГДА не вызывает POST `/git/refs`, git push, create/delete branch
  или force; missing/inaccessible ref не восстанавливает. Повтор исторического
  идентичного сообщения — только чтение. Stale head/HTTP 409/422 не повторяются.
- GitHub REST не принимает expected_old_sha в PATCH. Для двух sibling-коммитов
  от одного expected parent fast-forward пропускает один, отвергая другой.
  Это НЕ универсальная CAS-гарантия при внешних force/reset/recreate (ABA).
  Все участники должны соблюдать append-only; lifecycle-реестр, запрещающий
  повторное открытие удалённого task_id, ещё предстоит сделать. Сам адаптер
  вообще не имеет функции открытия беседы. Обычный git push не заменяет PATCH.
- Потерянный/повреждённый ACK или HTTP 5xx на PATCH — `publication_unknown`,
  без повтора. `reconcile(task_id, saved_message)` только читает текущую ветку,
  сверяет ПОЛНОЕ сообщение и возвращает Receipt SHA snapshot, содержащего его.
  `publication_not_observed` НЕ разрешает повтор внешнего эффекта. Успешный ACK
  без последующего чтения сообщения не является подтверждением публикации.
- Журнал автоматически не меняется и ничего не исполняет: интеграция
  result_ready -> append/reconcile -> mark_published — следующий отдельный этап.
  Receipt подтверждает наблюдение сообщения в конкретный момент, не вечное
  существование ветки. Отсутствие ветки после записи не восстанавливается.

Контракты сверены с официальной документацией GitHub 2026-10-26:
https://docs.github.com/en/rest/git/refs#update-a-reference и
https://docs.github.com/en/rest/git/commits#create-a-commit . HTTP-double моделирует
эти контракты, но НЕ доказывает фактическую семантику живого сервера.

У MT подтверждён только SSH; API-авторизации нет. Поэтому живой REST-транспорт
БЛОКИРОВАН до отдельно согласованной настройки repository-scoped Contents:write
авторизации или другого доказуемо update-only транспорта. Новые права не
запрашивались и не выдавались, SSH/токены не извлекались. Коннектор ChatGPT
используется только обычным разрешённым способом для публикации исходников;
его доступы не передаются агенту. До устранения блокера — только offline-тесты,
не установка и не пилот на реальных сообщениях.

Полный прогон: `PYTHONPATH=src python -m pytest tests -q`,
`python -m compileall -q src`. Набор блокирует сеть/subprocess и SQLite вне
tmp_path. Следующий шаг MT — локальный прогон ГОТОВОГО коммита без правок кода.

## 11. Первый live-шаг после прямого подтверждения владельца

Владелец напрямую разрешил продолжение поэтапного плана с уже настроенным
локальным PAT только для этого репозитория (Contents read/write, 7 дней).
Это снимает предыдущую паузу по авторизации, но НЕ разрешает создавать
ветку: создание `dialogs/<task_id>` требует отдельного явного поручения владельца.
При независимом чтении refs перед этим шагом веток `dialogs/*` не обнаружено.
Поэтому данный этап — LIVE READ-ONLY PREFLIGHT, не публикация и не установка.

Готовый `git_dialog.preflight` локально читает ТОЛЬКО явно указанную агентом
уже настроенную credential-file: regular, владелец текущий uid, mode0600,
до4096байт, final symlink отвергается; никаких поисков/chmod/создания credentials.
Путь и токен в отчёт/мост/Git не передаются. Запуск без shell tracing:
`PYTHONPATH=src python -m git_dialog.preflight <локально_настроенный_файл>`.
Значение токена не является CLI-аргументом и не печатается. Ошибки — только
машинные коды/HTTP status без traceback, путей или сырых ответов.

ReadOnlyTransport блокирует любые не-GET вызовы ДО реального HTTP. Скрипт
проверяет private repo, фиксированный main SHA до/после и загружает snapshot
ТОЛЬКО нашей беседы готовым GitHubDialog. При404 честный результат:
`missing_or_inaccessible`, публикация заблокирована до отдельного разрешения
создания ветки (и при необходимости проверки видимости). Ничего не создаёт,
не обновляет refs, не исполняет тела сообщений и не пишет SQLite.
Даже если ветка найдена, на этом шаге — только валидация чтения snapshot.
Успешный результат подтверждает только живое чтение через наш HTTP-код;
`write_transport=not_tested`. Это не доказательство PATCH/сквозного пилота.
Внешнее изменение main между чтениями приводит к отказу, скрипт main не меняет.

После фактического отчёта MT требуется ревью. При отсутствии ветки запрашивается
отдельное разрешение её создания, а не неявное создание из append. Работающий
мост/панель/службы/старые задачи и main этим этапом не изменяются.

## 12. Отдельно разрешённая инициализация и один update-only canary

После нового прямого поручения владельца ChatGPT создал и инициализировал
ТОЛЬКО `dialogs/task_3c2d37a2485b`. Это разовое разрешение НЕ является правом
автоматически создавать другие/повторно удалённые ветки. Исходное содержимое:
task.json (transport_canary_only), state revision1/seq1/next_actor=mt,
messages/1.chatgpt.json, kind=ack, ID chatgpt_canary_v1,
body GIT_DIALOG_CANARY_V1_CHATGPT_NO_EXECUTION. Kind=ack не принимается
ExecutionJournal.receive как исполняемая задача. Кодовой implementation-ветки
в дереве беседы нет; main не менялся. Жизненный цикл/удаление ещё не внедрены.

`git_dialog.live_probe` — готовый запуск MT ровно одного canary-report в уже
существующую беседу. Локально:
`PYTHONPATH=src python -m git_dialog.live_probe <локальный_файл_PAT> <initial_head>`.
PAT читается тем же безопасным reader, не печатается и не передаётся аргументом.
Один вызов GitHubDialog.append(actor=mt,kind=report,ID=mt_canary_v1,
body=GIT_DIALOG_CANARY_V1_MT_NO_EXECUTION); перед этим проверяется точный
initial_head, история/состояние и main, после — main и наблюдение report
реальным адаптером. Передаваемые bodies — только фиксированные несекретные
маркеры, не команды. Результат JSON не содержит paths/токен/сырые HTTPответы.

ProbeTransport запрещает создание/удаление refs, force и PATCH любой ветки
кроме dialogs/task_3c2d37a2485b. Допускает POST только Git trees/commits,
используемые адаптером для одного сообщения+state. При отсутствии ветки —
отказ, НЕ восстановление. При неизвестном исходе PATCH автоматического
повтора нет: вернуть ошибку и остановиться для независимой сверки Git.
Уже опубликованный идентичный canary-report при ручном повторном запуске
только наблюдается read-only. Никакого исполнения сообщений, SQLite,
worker/polling, установки, изменения моста/панели/служб или удаления.

Этот шаг проверяет ОДНУ реальную публикацию, но не доказывает живые гонки,
ABA/защиту от повторного открытия task_id или безопасное удаление ветки.
Отчёт MT ещё требует независимой сверки ветки/полного report/state/parent.
Следующие этапы только после ревью, без пакетной установки всего плана.

## 13. Durable outbox: сохранённый результат -> Git -> подтверждение

Новое прямое указание владельца разрешает автономно завершать согласованный
план без подтверждения каждого этапа. Это отменяет прежние паузы/лимиты
обменов, но не разрешает раскрывать токен, расширять права, менять main или
ломать действующий мост/службы/старые данные. Абсолютный нулевой риск не обещается.

`publication.publish_saved(journal, dialog, task_id, message_id, run_id)`
публикует ТОЛЬКО уже сохранённый immutable report. У функции нет callback
исполнителя и она не вызывает receive/start/save_result, subprocess или модель.
До отправки проверяет локального владельца/run/status/result, полное совпадение
исходного запроса с проверенной Git-историей, а также точный следующий ход report.
Сохраняет timestamp/body/ID/seq/reply_to из durable result, не генерирует их заново.

Журнал получил `publication_attempted` и атомарный `reserve_publication`:
True выдаётся только один раз и коммитится SQLite ДО remote write. Сбой после
резерва, до отправки, после PATCH, при потерянном ACK или до локального
mark_published не сбрасывает этот флаг. При восстановлении:
- точный report найден в проверенном snapshot -> локальный published;
- report отсутствует и попытка уже отмечена -> publication_unresolved, только
  диагностика/явное решение, без повторного POST/PATCH или исполнения;
- уже published, но Git больше не содержит report -> published_report_missing,
  не восстановление ветки и не resend.
В каждом случае статус подтверждается наблюдением сообщения, не ACK/Receipt
самим по себе. Конкурирующие наблюдатели сохраняют первое immutable commit SHA.

Совместимая миграция старой БД добавляет колонку в транзакции, сохраняя строки.
Старым строкам, уже вышедшим из received, консервативно ставится attempted=1:
старый формат не доказывает отсутствие прежней отправки. Такие строки можно
только сверить с Git, НЕ отправлять автоматически. К рабочим БД моста этот код
не подключается; на данном этапе миграция проверяется только в tmp_path.

Offline-интеграционные тесты используют реальный временный SQLite + FakeApi:
публикация, повтор/reopen, потерянный ACK, crash before send/after observation,
гонки резервов/координаторов, legacy migration, чужой run/input/result,
отсутствие ветки и ложный ACK. Это не установленный worker, не живой тест
журнала, не проверка удаления/ABA и не exactly-once для внешних действий.

## 14. Изолированный live journal-canary

После локального отчёта MT о142passed следующий этап подготовлен ChatGPT.
ChatGPT публикует в существующую беседу фиксированный requestseq3 kindtask,
IDjournal_request_v1, bodyGIT_JOURNAL_REQUEST_V1_NO_EXECUTION. Это тестовые данные,
не команда исполнителю. Прежние сообщения1/2 и task.json не меняются.

Готовый `git_dialog.journal_probe` запускается ТРЕМЯ отдельными процессами:
`prepare`, затем `send`, затем `observe`. Аргументы каждого: action,
локально настроенный credential-file, НОВЫЙ изолированный DB-path, requestHEAD.
DB-parent обязан быть существующим regular-dir0700 текущегоuid; файл БД600,
не symlink. Prepare создаёт файл только O_EXCL: существующая БД не открывается
для инициализации. Send/observe НЕ создают отсутствующую БД. К рабочим БД/venv/
службам модуль не подключается и их не мигрирует. Подготовленный тестовый
каталог/БД после отчёта сохраняются для диагностики, не удаляются автоматически.

- Prepare делает только remoteGET, строго проверяет requestHEAD/seq3/mode,
  сохраняет в новую БД исходный request, simulated start и ПРЕДВЫЧИСЛЕННЫЙ report
  journal_report_v1/seq4/GIT_JOURNAL_REPORT_V1_PRECOMPUTED_NO_EXECUTION, затем
  закрывает БД. Никакой body не исполняется и модель не запускается.
- Send повторно открывает ту же БД и использует publish_saved. Ожидается один
  update-only PATCH + atomic message/state и durable published/attempted1.
- Observe — отдельный повторный процесс с GET-only transport. Требует прежнюю
  attempted1; не может получить новый send grant. Проверяет существование и
  полное совпадение report, неизменность result hash, commit SHA и main.
  Проверяется exact parent requestHEAD при snapshotseq4. Никакого resend.

Каждый процесс выводит только безопасный JSON: action/status/attempted,
подтверждённыйSHA/resultSHA256/IDs/execution=none_precomputed_data_only/mainSHA.
Ни токен, ни credential-path, ни исходные HTTP-ответы/tracebacks не выводятся.
При любой ошибке не выполнять следующий mutating шаг, не повторять send.
Если send был неопределённым, observe допустим только для read-only сверки
той же БД и ветки; результат оценивает ChatGPT независимо чтением Git.

Этот этап проверяет сохранение/публикацию/перезапуск с тестовыми данными,
не устанавливает worker, polling, панель, службы или обработку команд.

## 15. Локальный lifecycle / tombstone (ТОЛЬКО offline на этом этапе)

Live journal-canary принят после фактического отчёта MT (152tests, три процесса)
и независимого чтения Git ChatGPT: reportseq4 journal_report_v1 в
commit b2d000797fa14072b76fdb6fb1ded9ac4f9638d1, parent requestHEAD
8e1f2e9ea1c99a47c2aa9746c779348f03113fd7. Изменены только state + message4;
весь прежний контент сохранён. Canonical _wire report SHA256 независимо равен
774206ada64eb93cfca2b3e9cf14ad3c004816d3b970c0295a3d472210a6d839,
одинаков с prepare/send/observe. Main не менялся. Это не установленный worker.

Новый `lifecycle.LifecycleRegistry` — ОТДЕЛЬНАЯ SQLite БД (не журнал выполнения
и не БД моста), synchronous FULL, собственный application_id, regular file600
текущегоuid; symlink/чужая БД отвергаются. Сам ничего не создаёт/удаляет в Git.

- Явный register фиксирует task_id и initial_head. Повтор активной регистрации
  с тем же initial_head идемпотентен, это НЕ право создать/восстановить ref.
  Неизвестная беседа не получает write grant. Cancelled/tombstoned ID повторно
  активировать нельзя, даже если БД закрывали и открывали заново.
- LifecycleDialog оборачивает append: BEGIN IMMEDIATE реестра держится от
  проверки active ДО завершения append. Close/cancel другого локального
  участника не может обогнать этот участок. Нельзя отпускать lock между
  проверкой и записью. При сбое lock освобождается rollback; cancel не означает
  отмену уже выполненного внешнего действия или exactly-once.
- request_delete — только начало ЯВНОГО намерения UI: state=closing, запись
  ожидаемого HEAD, TTL и SHA256 случайного nonce. Новые cooperating appends
  заблокированы. Nonce возвращается UI один раз, не в get/history/logs.
- confirm_delete требует nonce, непросроченное время и совпадение заново
  прочитанного HEAD. Сначала durable tombstone, затем возвращается
  DeletionTicket. Повтор подтверждения отвергается. Ticket НЕ выполняет DELETE,
  не подтверждает личность пользователя и не гарантирует conditional DELETE
  на GitHub. Будущий UI обязан аутентифицировать реального пользователя и
  получить отдельное подтверждение удаления; main удалять нельзя.
- abort_delete — явная отмена только НЕПОДТВЕРЖДЁННОГО намерения. Возвращает
  прежнее active/cancelled, инвалидирует nonce; cancelled не активируется,
  tombstone отменить нельзя. Git ref при этом не создаётся.
- Tombstoned значит «локально запрещены дальнейшие записи/переоткрытие»,
  НЕ «ветка удалена». remote_absent остаётся0 до отдельно проверенного
  отсутствия; ошибка/сбой удалителя не снимает tombstone. Сам метод фиксации
  remote_absent принимает отчёт вызывающего кода, не является независимой
  проверкой. Удалителя/REST DELETE и очистки реальных данных здесь НЕТ.

ВАЖНО: это локальная координация всех участников, использующих ОДНУ общую
durable SQLite БД. Не распределённый lock и не защита от произвольного внешнего
Git-клиента, удаления/пересоздания refs вне протокола или потери самого реестра.
Участники с независимыми реестрами так не синхронизируются. Нельзя обещать
защиту от ABA/физическое стирание объектов по одним этим тестам. Будущая
интеграция должна держать один авторитетный lifecycle-gateway для всех writers;
прямые записи ChatGPT также должны соблюдать lifecycle перед вводом удаления.
GitHubDialog пока не подключён к реальному реестру или UI. Рабочая ветка
беседы не закрывалась/не отменялась/не удалялась, никакие службы не менялись.

Offline-тесты проверяют durable reopen/tombstone, запрет повторного ID,
nonce/TTL/head/replay, отмену неподтверждённого UI-намерения, foreignDB/symlink,
блокировку позднего writer, lock ordering с двумя SQLite connections/threads,
исключение writer и read-only диагностику после локального cancel.

## 16. Ограниченный WorkerStep (пока ТОЛЬКО offline)

После фактического отчёта MT о176passed/compileallOK ChatGPT написал следующий
bounded step. Это не polling-loop, не systemd/cron и не установленный сервис.
Он привязан к одной явно настроенной task и S960b/agent-dialogs; lifecycle
обязателен, неизвестная/closing/cancelled/tombstoned беседа не начинает работу.

Допустимый JSON body имеет protocol=git-dialog-action, version=1 (строго int),
op. По умолчанию разрешённых действий НЕТ. Конфигурация может включить только:
- ping, без дополнительных полей -> фиксированное pong;
- sha256, с единственным аргументом text -> SHA256 именно текста в UTF-8,
  НЕ файла/пути/команды. Body до4096байт, text до2048байт.
Все неизвестные ops/поля/версии, duplicate keys, глубокий/повреждённый JSON,
неверные типы/UTF-8 и превышение размера отвергаются. Refusal содержит только
машинный код, не исходный текст. Никаких shell/eval/exec/import по body,
subprocess, callback-интерфейса, чтения файлов/токенов или запуска модели.

WorkerStep.step получает request/report/run IDs из конфигурации вызывающего
кода, не из body. В lifecycle write-транзакции проверяет snapshot/актуальный
запрос, сохраняет immutable input, получает одноразовый journal.start и
сохраняет результат чистого вычисления либо отказа. Только затем через
LifecycleDialog + publish_saved выполняет публикацию durable report.
На повторном вызове result_ready/published переиспользует точный результат,
не вычисляет заново; started/unknown -> worker_execution_unresolved без retry.
Внезапная ошибка вычисления оставляет started для явного разрешения,
не автоматически сбрасывает его. Saved result не пересоздаётся с новым timestamp.

Lifecycle gate держится через receive/start/compute/save и отдельно через
сам append. Отмена между сохранением и отправкой блокирует remote PATCH;
результат остаётся в журнале. Возможна консервативная отметка attempted=1
перед отказом локального lifecycle, потому что reserve записан раньше append;
это НЕ разрешает resend. Cancellation не отменяет уже начатое внешнее действие.
Модель external exactly-once/распределённая блокировка по-прежнему не обещается.

Offline тесты: default deny/строгая схема/инъекции как данные/хеширование текста;
durable refusal, повтор без evaluate, чужой run/report/repo, inactive lifecycle,
unregistered task, started/unknown без перезапуска, lostACK+reopen с GET-only
recovery, cancel между save/publish, два cooperating workers — одно вычисление
и один PATCH. Весь набор временный SQLite/FakeApi, сеть/subprocess запрещены.
Live bounded-worker ещё не запускался, службы/main/ветка беседы не менялись.

## 17. One-shot bounded worker canary (not a service)

`python -m git_dialog.worker_probe ACTION CONFIGURED_CREDENTIAL_FILE NEW_DIRECTORY REQUEST_HEAD`
is an opt-in, isolated runner. ACTION is exactly `prepare`, `step`, or `observe`.
Each action is a separate process. Credential value and path are never printed.
The directory must already be private (0700, current UID, not a final symlink).
Prepare requires TWO NEW absent files, journal.sqlite and lifecycle.sqlite,
created exclusively with 0600; partial failures are not permission to re-prepare.
These are never working bridge databases or the previous journal-canary database.

Pinned parent b2d000797fa14072b76fdb6fb1ded9ac4f9638d1 contains the previous
four immutable messages. Request is exactly seq5/worker_request_v1, kind task,
from chatgpt to mt, reply_to journal_report_v1, body
`{"protocol":"git-dialog-action","version":1,"op":"ping"}`.
The request commit must have exactly that one parent, and only state/new message
may differ. task.json and its transport_canary_only scope are preserved.
No body can select additional code, command, tool, path, credential, or operation.

Prepare is GET-only remotely, registers ONLY local metadata for the existing
dialogue, and records received without starting/evaluating a task. Step requires
that exact received row and active local registry, runs one WorkerStep with
allowed_ops=("ping",), and publishes the durable report via the existing outbox.
Report is seq6/worker_report_v1/reply_to worker_request_v1, body is the fixed
git-dialog-result with version1/oktrue/opping/valuepong. Step refuses a second
launch after any start, result, or publication attempt; no automatic retries.

Observe requires a previous durable result AND publication_attempted=1. It never
calls WorkerStep/start/evaluate. Its transport permits only GET. An exact saved
report may reconcile locally; missing/ambiguous publication does not authorize
resend. Current ref, exact report, sole parent, unchanged prefix/task/other files,
and main checkpoint are read and checked. started/unknown fails closed.
Output is limited to safe task/action/status/attempted/commit and input/result
SHA256; prepare result hash is null, step and observe must match. Preserve these
test databases after the report. On step failure do not repeat step/prepare;
one GET-only observe may diagnose the uncertain outcome before reporting it.

The offline suite includes three reopens, exactly one pure computation/PATCH,
lost ACK, rejected send without resend, inactive lifecycle, parent/file/ref
guards, old-file and symlink rejection. This runner is not installed polling,
not a shared distributed gateway, and not a proof of global lifecycle/exactly-once
external effects. No actual close/cancel/delete or working-service change.

## 18. Shared actor ports and bounded panel projection (offline component)

WriteGateway binds one private repository, one already registered task and an
explicit actor allowlist (empty by default). Ports bind the sender identity;
append/reconcile cannot select another task or sender. No registration, ref
creation/deletion, force flag, close message, execution, or network server API
is exposed by the gateway facade. Both actors use the SAME mandatory local
LifecycleDialog gate. Inactive local registry stops both senders. Read access
remains for diagnosis, not task revival. Normal transport still validates the
remote turn, immutable history and terminal state independently of local state.

WorkerStep accepts the exact ActorPort type only when its registry object and
actor match the journal configuration. It reuses the port's append gate rather
than nesting SQLite BEGIN IMMEDIATE. Other adapter inputs retain the original
mandatory LifecycleDialog wrapper. Offline integration covers a real WorkerStep
through MT's port plus a ChatGPT request through the other port, then dedup.

panel_view returns only structured data, bounded history (default20, maximum100),
pagination, exact direction and next actor. Last sender is green; next actor
yellow or red on error/no handoff for >=600s. This is NOT a process heartbeat or
proof of worker failure. Terminal dialogues show no waiting actor. Message/title
strings are untrusted data and MUST be rendered with text nodes, not innerHTML.
No web UI or working panel has been changed. delete_available is always false:
authenticated remote lifecycle has not yet been integrated.

ActorPort is trusted in-process configuration, NOT authentication or a sandbox.
This facade alone cannot prevent direct GitHub clients from bypassing the local
registry. A future deployed gateway must route ALL cooperating writers through
one authority. The offline terminal-close test demonstrates that an existing
remote close message blocks append even while the local registry is active;
it is NOT a live closing-barrier protocol, conditional DELETE, ABA solution or
global deletion proof. Before deletion, implement/review a durable remote closing
barrier and authenticated confirmation separately. No real branch is closed,
cancelled or deleted in this stage; main/working services remain untouched.

## 19. Durable terminal barrier coordinator (offline only, no deletion)

ClosingOutbox is a NEW separate SQLite database (own application_id/table,
FULL sync, current UID/regular file/0600/no final symlink, foreign DB rejected).
One immutable close intent per task: expected HEAD + full fixed close message,
durable attempted flag and confirmed commit. Reserve uses BEGIN IMMEDIATE;
exactly one process gets a send grant, committed BEFORE any remote mutation.
No reset, resend, replacement intent or deletion API exists.

ClosingCoordinator.prepare is a separate trusted lifecycle call, NOT an action
selected by a message body or actor port. It validates expected HEAD/turn,
prepares kind=close with fixed non-executable marker, persists the intent and
permanently cancels local publication. If stopped between the two local DBs,
publish re-establishes ONLY the local cancellation. An in-flight writer winning
that gap changes HEAD, so the old expected-parent barrier fails closed.

The remote barrier uses existing protocol kindclose -> statusdone. It is terminal,
not a reversible pending UI confirmation. Once observed it blocks append by all
cooperating protocol clients, even when their local registry is still active.
This does not stop already executed effects or arbitrary direct Git clients.
Expected sole-parent/no-force makes stale sibling commits mutually exclusive
under append-only refs. It does NOT provide universal CAS/ABA protection against
external rewind/recreation or guarantee physical Git object erasure.

publish first reads for an exact existing barrier; a durable prior attempt with
missing barrier yields closing_unresolved without another append. Reconciliation
checks full message, original task/history, expected terminal state, sole parent,
exact state/new-message blobs and all unchanged files. ACK alone is not accepted.
observe performs only remote reads and local confirmation, never reserve/append.
If a confirmed barrier disappears, closing_barrier_missing refuses repair.
Failure retains the permanent local cancel; user/operator resolution is required,
not automatic reopening. Read-only reconciliation remains available.

This component has NO CLI/service wiring, authentication, human confirmation or
remote DELETE. Actual panel deletion remains disabled. It is not sufficient to
deploy deletion: all writers must use a common authority and a separately reviewed
authenticated confirmation flow must define irreversible terminal intent versus
cancelable pending UI intent. No live dialogue is closed/cancelled/deleted here.
Offline tests cover separate DB reopen/lostACK/crash-before-send, cancel gap,
403/422/500/no-resend, falseACK, exact file guards, missing/rewound ref, foreignDB,
and a sibling writer versus closing barrier fast-forward race in FakeApi.

## 20. One-time terminal-intent confirmation controller (offline only)

ConfirmationStore uses a NEW separate private SQLite DB (directory0700,
file0600/currentUID/no final symlink/own application_id/table/FULL sync).
Pending rows bind task, configured owner hash, nonce hash, expected HEAD, actor,
issued/expiry times and close-message ID. Raw nonce is returned once to the
caller; status excludes hashes/nonce/owner. It must never enter history or logs.
Reissue invalidates a pending nonce, but cannot replace another owner's intent
or reopen a consumed task ID. TTL is 1..600 seconds; default300.

ConfirmationController is NOT authentication. Its principal argument MUST come
from a separately authenticated server session with CSRF/origin validation,
not request-body/Git fields. Matching a configured string does not prove a human
clicked a button. No web route or live integration is enabled by this component.

request reads the current existing dialogue HEAD and issues pending intent only.
It does not cancel/block workers or touch the closing outbox. cancel consumes no
remote lifecycle authority and leaves the task active. confirm checks the bound
owner, nonce, TTL, and independently loaded current HEAD; durable BEGIN IMMEDIATE
changes pending->consumed exactly once BEFORE ClosingCoordinator.prepare.
Only then is a fixed terminal intent prepared and local cancellation made
permanent. confirm does NOT publish a barrier or delete a Git ref.

A crash after durable consume but before prepare is intentionally unresolved:
repeat confirm is denied even when no remote effect occurred. Read-only diagnosis
and explicit operator resolution are required; no automatic replay/reset/undo.
Stale HEAD or expired/wrong nonce before consumption leaves pending intent but
does not authorize a new head. The UI must request a new explicit confirmation.

Offline tests cover wrong owner/task/nonce, expiry/future clock/stale head,
reissue, cancelled pending intent, redacted status, DB reopen, crash gap, foreign
DB/symlink rejection, and two consumers racing for one durable consumption.
Existing panel delete remains disabled. Authentication/HTTP/CSRF/UI integration
and a shared deployed authority for all writers still require separate review.
No real task/registry/outbox/credential/service is used by these tests.

## 21. Offline RPC access boundary (not a network server or login system)

AccessAuthority binds principals/roles/actors ONLY from trusted host configuration.
It defaults to no bindings. Trusted issue() creates random opaque session+CSRF
grants after a separately verified login; there is NO login/bootstrap RPC route.
Only hashes are retained in bounded memory (128 grants, TTL1..900/default300),
with revoke and fail-closed expiry/restart. These are not PATs, cookies, files or
external account changes. Tests use ephemeral grants only. issue() must NEVER be
exposed without authentication. Possession of a grant is not human-presence proof.

Dispatcher accepts exactly POST /api/task, one configured Host and exact Origin.
Plain HTTP configuration is loopback-only, otherwise HTTPS required. It rejects
duplicate/control/proxy/framing/unknown headers, missing or invalid Bearer/CSRF,
cross-session CSRF, expired grants, non-JSON/invalidUTF8/duplicateJSON/NaN/deep
or >8192-byte bodies, Content-Length mismatch and unknown/extraneous RPC fields.
Host/Origin are defense-in-depth, not authentication. A future HTTP parser must
reject malformed framing before dispatch and set no-store/security headers;
no listener is installed or exercised here.

Mutations default disabled. view returns bounded panel data. append is allowed
only for a configured agent; sender comes from its server-side actor binding,
not JSON. Owner is not an agent and cannot impersonate one. Owner-only
request_close/cancel_close/confirm_close call the confirmation controller with
principal obtained from the authority. Agent roles cannot call those operations.
No RPC for issue/revoke/login/roles/task creation/closing publish/ref deletion,
shell or arbitrary tools. confirm_close still ONLY prepares an intent, never
publishes or deletes; confirmation nonce and session/CSRF must be redacted from
all logs/history. Message data cannot manufacture owner authority.

Offline tests integrate FakeApi + separate temporary DBs, real gateway/confirmation
objects and ephemeral authority grants. They prove these local rules, not an
authenticated deployed UI or browser-user interaction. Existing bridge/panel,
services, PAT and real dialogue are unchanged; delete remains disabled. A live
gateway requires separately reviewed authenticated bootstrap and ALL cooperating
writers routed through the common authority, with no bypass of protected services.

## 22. Bounded read-only HTML panel component (not a deployed panel)

panel.render_panel validates a complete snapshot and renders one conversation row,
direction arrow, actor status colors, next actor and paginated history. Default20,
maximum20 messages per page; body preview2048 characters, explicitly marked when
truncated. Complete messages remain in storage. Output is capped at256KiB.
All dynamic fields are HTML-escaped, never script/HTML content, class names or URLs.
Navigation targets are fixed local paths with validated integer page parameters.
No JavaScript, forms, images, external fonts or resource fetching.

History is a details/summary control; the delete button is placed below it and
ALWAYS disabled. The page says it is isolated read-only, not the working bridge.
Timeout is labelled lack of handoff, not proof a process died. No queue column.
No physical Git erasure is promised.

security_headers returns no-store/nosniff/frameDENY/no-referrer plus restrictive
CSP default-srcnone, hash-authorized static CSS only (no unsafe-inline/scripts),
base-uri/form-action/frame-ancestorsnone. preview_response is a PURE GET router,
not a network listener: exact configured Host, fixed / and /history paths,
bounded nonduplicate offset/limit query; POST/DELETE/RPC/path/URL tricks rejected.
Host alone is not authentication; this adapter is for synthetic test data only
until a reviewed authenticated HTTP integration exists.

canary_snapshot constructs exactly two synthetic nonsecret markers under virtual
task_preview_fixture/head f*40. It does not create a branch/task, load real data,
read files/credentials/DBs, call GitHub, evaluate messages or start a service.
It is intended for a later bounded loopback preview smoke, not live history.

Offline tests parse HTML tags and check XSS/title escaping, fixed pagination,
size/truncation, disabled delete placement, CSP style hash, route/Host/method/
query denials, timeout wording and self-contained synthetic fixture. No actual
network/server/browser/working panel or real credentials were used in this stage.

## 23. Bounded synthetic loopback HTTP self-test (not a preview service)

`PYTHONPATH=src python -m git_dialog.preview_smoke --self-test` is the ONLY CLI
mode. No address/port/path/data/token input, standalone serve mode or file IO.
It binds only 127.0.0.1:0 (ephemeral; privileged/8787 ports refused), uses the
two fixed canary_snapshot markers and fixed clock, then exits. No PAT/GitHub,
SQLite, real history, grants, login, RPC, worker, installation or Funnel.

One non-daemon server thread: max12 iterations/deadline10s, accept timeout0.2s,
client timeout1s, accepted connection timeout0.5s. An independent absolute12s
watchdog shuts down/closes ONLY the owned active connection and listener, also
handling slow trickling headers. stop/finally joins thread and closes socket;
there is no persistent/background service or cron. No request/body/header logs.

HTTP/1.0 connection-close responses; exact Host/single Host, max16 headers,
bounded header values, no body (Content-Length only0/at most once), no Transfer-
Encoding/Expect. Methods other than GET refused; unknown-method errors sanitized.
Fixed GET paths/pagination and security headers delegated to pure panel router.
It is synthetic test data only; Host is not authentication and this is NOT a
general-purpose production HTTP server or a protected real-data panel.

Seven direct http.client checks (no proxies/DNS/external hosts): GETroot200,
GEThistory200, POST405, DELETE405, RPC404, wrongHost403, invalidpage400.
Each verifies exact security headers, response size and root body against the
independent pure renderer. RootSHA256 at fixed clock is
05f7537abe6f81a4ef34d1a3789c2a80dcd9fb1dc5a79ab5e78ade60fbf21e11.
Safe JSON reports only statuses/hash/scope/shutdown, not HTML or secrets.

Offline tests use memory sockets/BytesIO only under the normal network-blocking
fixture: framing/duplicate Host/body/Expect, method denial, sanitized errors,
connection-close/pipeline, CSP/security headers, wrong Host, CLI guard, watchdog
ownership and reproducible root hash. The explicit self-test runs OUTSIDE pytest
only after offline tests pass; one execution per approved ready-code instruction.



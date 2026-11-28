# ChatGPT ↔ MT Git-мост: ПАМЯТКА ДЛЯ НОВОГО ЧАТА

**Сначала прочитать этот файл, затем актуальные схемы MCP.** Репозиторий `S960b/agent-dialogs`, одна задача = ветка `dialogs/<task_id>`. `main` — код и документация, **не** переписка. Эта памятка относится к проверенному интерфейсу **6 ноября 2026**. При изменениях инструментов нужно снова проверить реальные схемы; старые P30/P32/P34 инструкции не являются источником текущих имён.

## 1. Текущие инструменты (не использовать выдуманные имена)

| MCP Hermes | Значение |
| --- | --- |
| `dialog_create(request_id,title)` | Создать **новую** задачу лишь если старый task_id не задан |
| `dialog_creation_status(request_id)` | GET-проверка неопределённого создания, тот же ID |
| `dialog_list(offset,limit)` | Список без сообщений |
| `dialog_index(task_id,offset,limit,order)` | **Только индекс**, seq/message_id и Git HEAD, НЕ body |
| `dialog_health(task_id)` | Read-only status, waiting_for, HEAD |
| `dialog_notice(task_id,head,message_id,seq)` | Проверяет УЖЕ опубликованный Git-коммит, НЕ пишет сообщение |

**В этом интерфейсе НЕТ `dialog_view` и `dialog_append`.** Эти имена в старой документации являются историческими. Если в другой сессии действительно доступен авторизованный `dialog_append`, предпочесть его ручной Git-публикации. **Не пользоваться** старым queue `bridge_*`, `job_id` или другим диалогом при ошибке.

## 2. Как прочитать ответ МТ

1. Получить от пользователя точный `task_id`. **Не создавать новый и не выбирать «последний».**
2. Через Hermes `dialog_index(task_id,limit=3,order="newest")` и `dialog_health(task_id)` узнать HEAD, seq и чей ход.
3. Через подключённый GitHub `fetch_file(repository_full_name="S960b/agent-dialogs",path="messages/<seq>.<actor>.json",ref=<HEAD>)` получить **body** (в `dialog_index` тел нет). Для старых сообщений использовать тот же pin HEAD.
4. Если ход `waiting_for=chatgpt` и сообщение требует ответа, готовить следующий seq. При `waiting_for=mt` **не публиковать** ChatGPT-сообщение.

## 3. Точный формат исходящего сообщения

Сохранить файл в ветке `dialogs/<task_id>` по пути `messages/<N+1>.chatgpt.json`:

~~~json
{
  "protocol": "git-dialog",
  "version": "1",
  "task_id": "task_...",
  "message_id": "cgpt_unique_stable_v1",
  "seq": 7,
  "from": "chatgpt",
  "to": "mt",
  "reply_to": "previous_message_id",
  "kind": "task",
  "created_at_utc": "2026-11-06T12:43:18Z",
  "body": "Задача агенту"
}
~~~

В `state.json` (единственный второй изменяемый файл): `revision=R+1`, `seq=N+1`, `last_message_id=новый`, `next_actor="mt"`, `status="working"` если раньше `waiting` (иначе сохраняется действующий статус), `protocol/version/task_id` неизменны. `reply_to` точно равен последнему `message_id`. Первая запись в пустой истории имеет `reply_to:null`.

**Критический баг 6 ноября:** JS `new Date().toISOString()` даёт запрещённые миллисекунды (`.562Z`). Вместо него **`new Date().toISOString().slice(0,19) + "Z"`**. Python: `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`. Только точное `YYYY-MM-DDTHH:MM:SSZ` в UTC; `+00:00` тоже запрещено.

Проверить ВСЁ **до первого Git POST**: `task_id` regexp `^[a-z0-9][a-z0-9_-]{2,63}$`; `message_id` и `reply_to` regexp `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`; timestamp по формату и календарю; body ≤ 65 536 байт UTF-8; целочисленные seq/revision; `from/to`; `kind` (для обычного задания `task`); существующая история без дыр/дубликатов; корректный `last_message_id`; допустимый ход; совпадение текущего HEAD; статус не `closing/done`. Не делать `return/close` вручную. Проверить, что этот `message_id` ещё не опубликован (при неопределённости нельзя создавать новый ID).

## 4. Обязательный preflight для ручной публикации

Есть read-only валидатор `scripts/preflight_chatgpt_append.py`, который вызывает **основной** `git_dialog.parse_snapshot` и `prepare_append` и проверяет новую историю до каких-либо Git writes. На изолированной копии репозитория:

~~~sh
PYTHONPATH=src python scripts/preflight_chatgpt_append.py < /tmp/chatgpt-preflight.json > /tmp/chatgpt-candidate.json
~~~

Файл ввода — JSON с **пятью** ключами: `head` (фактический 40-hex HEAD), `task` (реальный `task.json`), `state` (реальный `state.json`), `messages` (все предшествующие сообщения как JSON-объекты в порядке seq) и `append`. Пример последнего ключа:

~~~json
{
  "task_id": "task_...",
  "head": "<CURRENT_40_HEX_HEAD>",
  "message_id": "cgpt_unique_stable_v1",
  "kind": "task",
  "body": "Задача агенту",
  "created_at_utc": "2026-11-06T12:43:18Z"
}
~~~

Поле `created_at_utc` можно опустить: валидатор создаст правильные целые секунды. На выходе: `ok:true`, `message_path`, `message`, `state`, `commit_parent`. **Любая ошибка / неверное состояние / отсутствие полной проверки = STOP, не создавать Git blob/tree/commit.** Вызов этого скрипта не предоставляет права записи, не включает автоматической защиты всех GitHub writers.

В ChatGPT Code Mode не всегда есть доступ к Python на Kali. Тогда эквивалентный строгий preflight нужно выполнить **локально в коде до Git POST**, включая сравнение с полным snapshot; если невозможно — **остановиться**, не надеяться, что `dialog_notice` всё исправит. Никогда не называть просто форматную памятку серверной проверкой/branch protection.

## 5. Публикация, когда доступны только index/notice

1. Прочитать фактическую Git ветку `dialogs/<task_id>` и HEAD `H`; сверить с Hermes `dialog_index`/`health` и проверенным snapshot. Не писать при конфликте, ошибке authority или неизвестной попытке.
2. По **прошедшему** preflight создать Git blob `message` и `state`, Git tree с base_tree от родительского commit `H`, изменяющий только `messages/<N+1>.chatgpt.json` и `state.json`. Создать commit **с одним parent `H`**, не от `main`.
3. **До update_ref** получить кандидат по точному commit SHA и проверить его содержимое и полный snapshot, точные два изменённых пути, единственного parent `H`. Невалиден — не публиковать.
4. Ещё раз сверить живой HEAD; обновить `dialogs/<task_id>` только fast-forward CAS: `update_ref(branch_name="dialogs/<task_id>",sha=candidate,expected_sha=H,force=false)`. Не делать `force` и не повторять POST при неоднозначном результате. Проверить живой ref и точный файл через GET.
5. После подтверждённого HEAD вызвать `dialog_notice(task_id=<task_id>,head=<new_commit_sha>,message_id=<same_id>,seq=<N+1>)`. **Никакого body, никаких старых параметров bridge.** Сверить `observed:true`, `dialog_index` и `dialog_health`.
6. В статусе строго разделять **Git записан** / **authority увидела (`observed:true`)** / **MT inbox реально доставил** (`seen=seq`, нет проблемного `inflight`) / **MT ответил**. `observed:true` само по себе ещё не означает доставку в сессию.

Этот fallback обходит штатный AppendStore и не гарантирует полноценную идемпотентность. Его нельзя считать эквивалентом `dialog_append` или использовать для массовых операций. Не переписывать прошлые сообщения, не делать force/reset без отдельного разрешённого восстановления.

## 6. Ошибки

`authority_unavailable` — **общий код**, не доказательство, что bridge процесс упал. Проверить `dialog_list` и `dialog_health` другого известного диалога, затем целостность последнего JSON в Git и timestamp; ошибка одного диалога может быть результатом **невалидного snapshot**. При диагностике сначала только GET. Если после Git-записи `notice` неуспешен, **не создавать новую задачу или сообщение под новым ID**, не дублировать побочные действия. Проверить текущий commit и обсудить безопасное восстановление.

Исторический инцидент: `task_7a8b5cb59b7a1d40bea669d342b149a5`, seq 7, `message_id=cgpt_news_p2_cli_safety_probe_20261106_v1`, исправленный commit `7a2cc285f77259c0bfe1d7e5fab8420189c2db33`: `dialog_notice` вернул `observed:true`. **Это не текущий HEAD на будущее и не разрешение послать seq 7 ещё раз.**

### Подтверждённый инцидент 9 ноября 2026: невалидная метка в seq 21

В `task_760cfd0ff32f51a5c99ad18ef08613a5` прямой Git-публикатор повторно совершил ошибку `new Date().toISOString()`: `created_at_utc="2026-11-09T15:45:47.561Z"` (три лишние цифры). `parse_message/validate_timestamp` отвергает метку, поэтому `dialog_index`, `dialog_health` и Taskboard History могли стать недоступны **при сохранённой ветке Git и 21 файле сообщений**. Общий `authority_unavailable` **не доказывает**, что упал Unix-сервис; причина может быть в невалидном snapshot.

После явного разрешения владельца поле исправлено на `2026-11-09T15:45:47Z` через **новый fast-forward repair-коммит** `bd019713bd03b1b823272f411c9750360475db02` (родитель `58a88c9449edfc55069b025d14151bd55a2bfad4`). Diff содержит **только `messages/21.chatgpt.json`**, все прежние сообщения, body и `state.json` сохранены; force/reset не использовались. Этот repair — исключение по разрешению владельца, **не** разрешение регулярно изменять уже опубликованные сообщения.

Для будущих публикаций правило обязательное: построить целосекундный UTC timestamp **до** сериализации; выполнить полный preflight **до Git POST**; проверить неизменяемый candidate перед CAS; сравнить live HEAD, обновить только `force=false`; подтвердить чтение после публикации. Не считать исправление датой доставки: `dialog_notice` и приём MT нужно проверять отдельно после восстановления authority. Исправление/нормализация *кода* — отдельная будущая задача, не часть инцидентного исправления.

## 7. Запомнить при переходе в новый чат

Прочесть: `AGENTS.md` → **этот документ** → `PROTOCOL.md` (формат) → `docs/P325/README.md` (доставка MT). Найти **текущие** инструменты, затем продолжить только заданный `task_id`. Никаких «аварийных» отправок через legacy queue и никакого нового диалога для продолжения старого.
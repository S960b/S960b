# P30: готовая установка разделения диалогов

Исполнитель — MT, автор кода — ChatGPT. Один проход, STOP на первой ошибке,
неизвестном результате, неожиданном stderr или несоответствии guard. Никаких
исправлений, chmod, повторов, новых секретов/прав/панелей/сервисов, сброса баз,
поиска потерянных путей или самостоятельной доработки кода.

Точный CANDIDATE и TREE берутся **только** из подтверждённой публикации ChatGPT,
приложенной к поручению. Их нельзя пересчитывать/подменять по движению ветки.
Ожидаемый единственный родитель: `98c91a963f10d6ec567bd946d32b7e5be369c89a`.
Репозиторий прежний private `S960b/agent-dialogs`, ветка реализации прежняя
`implementation/git-dialog-v1`. Изменение native bridge_server.py не нужно.

## A. Проверка кандидата в уже известной тестовой копии

Использовать только удержанную ранее проверенную копию и её известный testenv.
Если привязка потеряна — STOP/binding_unavailable, без поиска/новой копии/venv.
Установленные зависимости не менять. До fetch require tracked clean и HEAD
равен 5ec421568b379a8470a5b7ac7ad0ad9527cc6005 либо 98c91a963f10d6ec567bd946d32b7e5be369c89a.
Обычный fetch существующего origin и fast-forward **точно CANDIDATE**, без reset,
force, новых веток, обхода auth, изменений main или веток переписки. Require
полные HEAD/sole parent/TREE совпали с поручением, tracked clean.

Тем же known testenv, cwd этой копии, umask002, env без APPDIR/APPIMAGE,
PYTHONPATH=src: root `python -m pytest tests -q`, затем
`python -m compileall -q src tests deploy`. Оба exit0 и tracked clean.
Ничего не импортировать из original bridge. Standalone IPC/HTTP fixture не
запускать, старые транспортные/worker/canary доказательства не повторять.

## B. До live-изменения

Долговечная копия `/home/kali/agent-dialog-beta/code`: tracked clean, HEAD равен
одному из двух допущенных A checkpoints, существующая `.venv-beta` сохраняется.
Никаких зависимостей/копирования venv. Только безопасные properties известной
`hermes-bridge.service`: active/running, Result success, ExecMainStatus0,
NRestarts0, Restart=no; запомнить реальный текущий MainPID (не считать прежний
1541974 актуальным после диагностического рестарта), порт8765 принадлежит ему.
Существующие native drop-ins только91+92. Не читать их содержимое/ExecStart/env.

Известный original `/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py`:
regular644/currentUID/nlink1, stable SHA256
`48f3a710904269b1f0f335a98c0e151e509fb0c39d661a2fdf2e0aef93e846e9`.
Только metadata/hash, не аудит исходника. Существующие STATE700, registry,
AppendStore, authority-worker, authority-events и обе authority sockets600
сохранить. Новый `/home/kali/agent-dialog-beta/state/authority-creations.p30.sqlite`
должен быть ABSENT/no symlink. Если уже существует — STOP/preserve, не открывать,
не усыновлять, не удалять/пересоздавать его под видом чистой установки.
Панель8791 должна оставаться stopped, чужие8789/8790/8787 не трогать.

## C. Код и один рестарт

В долговечной копии тот же normal fetch и fast-forward **точно CANDIDATE**,
require full HEAD/sole parent/TREE/clean. Существующая .venv-beta неизменна.
Никаких native source/unit/credential/subscription/OAuth/Queue изменений.
Один `systemctl --user restart hermes-bridge.service`. Одно ожидание готовности
не дольше120s, затем те же safe properties: active/running/success/status0,
NRestarts0/Restart=no/91+92, новый MainPID и8765 того же PID. Native SHA/mode
не изменились; registry/AppendStore/worker/events/история/backup сохранены.

Новая creation DB должна быть regular600/currentUID/nlink1, обе authority
sockets600, STATE700. Только metadata, без SQL dump. Не создавать базу вручную.
Если ошибка — STOP, сохранить состояние, без второго рестарта/восстановления/
правки исходника/удаления журнала/повторного создания/расследования секретов.

## D. Одна проверка создания от MT

Только C полностью green. Из verified CANDIDATE-копии тем же known testenv и
окружением, один вызов существующего `python -m git_dialog.unix_authority cli`,
stdin EXACT JSON:

```json
{"op":"create_dialog","request_id":"multi_branch_mt_20261030_01","title":"Отдельный диалог MT — проверка P30"}
```

Outer CLI budget150s, максимум5s termination grace. Это разрешение только на
одну новую отдельную Git-ветку и служебные task/state, не выполнение переписки.
Require status=confirmed, created=true, initiator=mt, waiting_for=mt, фактические
task_id/branch/fullhead. Затем ОДНО чтение через тот же CLI с фактически полученным
task_id: op=view, offset0, limit1, order=newest. Require та же ветка/head,
total_messages0, waiting_forMT. Ни append, ни worker/canary, ни код по сообщению.

Неопределённый create/timeout: STOP, request ID сохранить; НЕЛЬЗЯ повторять create,
генерировать новый ID или автоматически вызывать статус после ошибки. ChatGPT
назначит при необходимости только GET reconciliation `creation_status` с тем же
request ID. Не удалять проверочную ветку.

## E. Результат

В существующий owned completion/MT-чат: полные candidate/parent/tree/clean,
pytest count/time/exit, compile0, safe startup properties/новый PID, source hash
неизменен, новые DB/socket metadata, точные D receipt+view (без секретов/логов).
Не публиковать отчёт в старый Git-диалог: его ход принадлежит ChatGPT.
Не создавать дополнительный Queue-report/CLI send/ACK/ручное событие.

После успешной установки клиенту нужны обновлённые MCP-метаданные: пять
инструментов из MULTI-DIALOG.md, у view/append обязательный task_id. Если клиент
всё ещё видит старую двухинструментную схему — сообщить техническую необходимость
обновления, не создавать обходной RPC через Queue или GitHub. Следующий тест
ChatGPT — создание **другого** нового диалога из обычного чата после refresh.
Установка не доказывает автоматический MT-получатель или интеграцию UI8789.

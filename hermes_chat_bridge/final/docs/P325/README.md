# P32.5: как устроен Git-мост ChatGPT и MultiTool

> **Актуализация 06.11.2026:** для реальной отправки в новом чате вначале читать [корневую памятку](../../CHATGPT_GIT_BRIDGE.md). Проверенный MCP сейчас: `dialog_create`, `dialog_creation_status`, `dialog_list`, `dialog_index`, `dialog_health`, `dialog_notice`. Указания ниже о `dialog_view`/`dialog_append` относятся к историческому API до обновления интерфейса. `dialog_index` не выдаёт body; `dialog_notice` не записывает Git и не подтверждает доставку MT. Ограниченный GitHub fallback требует preflight **до** любого Git POST. 


**Статус на 4 ноября 2026: P32.5, P33, P34 и P35 установлены.** Подтверждено последующими проверками MT: P33 перезапуск и Unix-сокеты работают; P34 оставил только шесть `dialog_*` MCP-инструментов; P35 добавил колонку «Последнее действие» в Taskboard. Основной код сохранён в `feature/p35-taskboard-installed` (`edca980ec8dddff00d625fff444a4d9a9ffd61c6`), а P34 применён к отдельному живому `bridge_server.py` в репозитории `S960b/S960b`.

**Историческая справка P32.5 (снимок отчёта MT №84 от 3 ноября 2026 года).** Источник: приватный репозиторий S960b/agent-dialogs. Подробная историческая спецификация находится в PROTOCOL.md, MULTI-DIALOG.md, COMMON-AUTHORITY.md и docs/P32_TASKBOARD.md. При расхождении с работающим кодом доверять проверенной версии исходника и фактическому локальному состоянию, а не старым текстам.

## Суть проекта

Нужен постоянный канал ChatGPT ↔ локальный MultiTool (MT) для выполнения технических задач, прежде всего CTF. GitHub хранит сообщения и последовательность ходов. Локальная authority и MCP Hermes обеспечивают проверку прав, публикацию и чтение. Плагин в MultiTool доставляет новые задачи в закреплённую MT-сессию. Он **не** исполняет произвольный код из Git сам по себе: текст поступает как поручение агенту.

~~~text
ChatGPT → MCP Hermes (dialog_index / dialog_health)
        → GitHub connector: read pinned HEAD, validate candidate, Git commit/CAS
        → MCP Hermes dialog_notice (observation of existing commit)
        → локальная authority (authority-cg.sock)
        → приватный GitHub: S960b/agent-dialogs, ветка dialogs/<task_id>
        → MT Git Inbox plugin: Unix CLI → authority-mt.sock
        → существующая MT-сессия (Chat-45)
        → MT append(report) в ту же Git-ветку
        → ChatGPT получает индекс через dialog_index, body через GitHub fetch_file

Панель владельца: http://127.0.0.1:8787 (локальный loopback).
Bridge MCP: ~127.0.0.1:8765.
~~~

## Три слоя, которые нельзя путать

1. **Git-диалог.** Один task_id = одна ветка dialogs/<task_id>. Ветка содержит task.json, state.json и messages/<seq>.<actor>.json. История коммитов неизменяема; main не содержит активных переписок.
2. **Authority и ограниченный fallback.** В историческом API ChatGPT вызывал авторизованный `dialog_append`. В текущем MCP доступен только `dialog_notice` для уже опубликованного Git-коммита, а `dialog_index` отдаёт метаданные. В этом режиме ChatGPT использует GitHub connector лишь после обязательной локальной preflight-валидации полного snapshot и проверяет candidate до CAS; такой путь **обходит AppendStore**, не даёт его гарантий и не считается полным эквивалентом writer.
3. **Доставка в MultiTool.** Локальный ~/.local/state/mt-git-inbox/state.json хранит sessions (куда доставить), seen (до какого seq доставлено) и inflight (зарезервированную, возможно незавершённую доставку). Этот файл отличается от state.json, записанного в Git-ветке.

Legacy `bridge_put_message` / `bridge_get_message` / `bridge_put_reply` с `job_id` — **старый** queue-механизм. После установки P34 эти методы больше НЕ зарегистрированы в MCP. Используйте только `dialog_*`; старые внутренние классы ещё могут присутствовать в исходнике, но не являются рабочим публичным API.

## Полный маршрут ChatGPT → MT (текущий MCP index/notice)

1. Сначала прочитать [CHATGPT_GIT_BRIDGE.md](../../CHATGPT_GIT_BRIDGE.md). По **точному** task_id вызвать `dialog_index` и `dialog_health`, взять HEAD/seq/waiting_for. Содержимое сообщения прочитать через GitHub `fetch_file` по этому HEAD.
2. Если ход у ChatGPT, собрать сообщение и state по протоколу; обязательна **предварительная** проверка полного snapshot через `scripts/preflight_chatgpt_append.py` или эквивалентное строгое выполнение `git_dialog.parse_snapshot`/`prepare_append`. Никаких Git POST при ошибке. Timestamp только `YYYY-MM-DDTHH:MM:SSZ` без миллисекунд; body максимум 64 KiB UTF-8.
3. По проверенному результату создать один Git-коммит, меняющий ровно новый `messages/<seq>.chatgpt.json` и `state.json`. Перед движением ref проверить candidate. Обновить только существующий ref `dialogs/<task_id>` по ожидаемому прежнему HEAD, fast-forward `force=false`.
4. Когда Git commit независимо подтверждён, вызвать `dialog_notice(task_id,head,message_id,seq)`, сверить `observed:true` и индекс. Это подтверждение authority **не равно** фактической доставке в MT-сессию.
5. MT-плагин приблизительно раз в минуту использует Unix CLI и доставляет последнее адресованное MT сообщение, резервируя `inflight` перед `session.prompt`. После подтверждённой доставки отмечает `seen[task_id]=seq` и снимает `inflight`. При неопределённом исходе не повторять исполнение без reconcile.

Если в новой сессии доступен авторизованный `dialog_append`, он предпочтительнее ручного Git-пути, и следует использовать его актуальную схему. Нельзя автоматически возвращаться к legacy `bridge_*`.

## Маршрут MT → ChatGPT

MT готовит отчёт, затем через тот же Unix CLI читает фактический task_id, head, waiting_for и пишет append с kind=report, уникальным message_id и body. Authority публикует отчёт в ту же Git-ветку. ChatGPT читает индекс через `dialog_index`, а тело отчёта через GitHub `fetch_file` по подтверждённому HEAD. MT не может писать от имени ChatGPT, равно как ChatGPT не должен писать от имени MT.

MT CLI из корня проекта:

~~~bash
cd /home/kali/agent-dialog-beta/code
printf '%s\n' '{"op":"view","task_id":"task_...","offset":0,"limit":2,"order":"newest"}' |
  PYTHONPATH=src .venv-beta/bin/python -m git_dialog.unix_authority cli
~~~

Перед записью MT передаёт op=append с task_id, актуальным head, message_id, kind и body.

Новый диалог создаётся **отдельной** операцией dialog_create(request_id, title), которая возвращает новый task_id, branch и head. При непонятном результате только dialog_creation_status с тем же request_id. Не выбирать автоматически пилотную беседу или «последнюю ветку».

## Панель и возврат хода

Локальная taskboard на 127.0.0.1:8787 показывает диалоги и историю; доступ защищён отдельной авторизацией. Кнопка «Вернуть ход» относится только к **последнему** сообщению: return_turn проверяет head и last_message_id и публикует специальный возврат. Он не переписывает Git-историю; Inbox не должен трактовать return как новую задачу. Удаление ветки требует отдельного подтверждения владельца и не используется как smoke-test.

## Где смотреть живую систему

| Назначение | Путь / адрес |
|---|---|
| Код моста | /home/kali/agent-dialog-beta/code |
| State authority и Unix sockets | /home/kali/agent-dialog-beta/state |
| Сокеты | authority-cg.sock, authority-mt.sock внутри state |
| Рабочий MT-плагин | /home/kali/.config/gigatool/plugins/mt-git-inbox.mjs |
| Локальное состояние плагина | /home/kali/.local/state/mt-git-inbox/state.json |
| Лог доставки | /home/kali/.local/state/mt-git-inbox/inbox.log |
| База сессий MT | /home/kali/.local/share/gigatool/opencode.db |
| Релизные скрипты (локально, не гарантированы Git) | /home/kali/.local/share/mt-release/ |
| Панель | http://127.0.0.1:8787 |
| GitHub | https://github.com/S960b/agent-dialogs |

Доступность файлов, токенов, БД и сервисов на новой машине не гарантирована: Git хранит код и сообщения, **не** локальные процессы, пароли, OAuth, секреты, сокеты, seen/inflight или opencode.db.

## Фактически установленная версия P32.5 (отчёт MT №82)

После подготовки релиза (отчёт №80) владелец выполнил установку, затем MT проверил её в отчёте №82 и завершил приёмку в **отчёте-замене №84** основного Git-диалога `task_df06d95a56c65e80d8a2245386cb2e97`.

| Что проверено | Результат |
|---|---|
| Исходник | `feature/p325-integrate-fixes`, Git-коммит **896e189701016b6bfbf2a2932859825d3ab496cc** |
| SHA256 установленного файла | **334f75d4223731aff039b1d67e3764e0f16cf78653c9294f78713bd6306393e1**, полностью совпадает с Git-исходником |
| Файл и процесс | Файл 30 130 байт, mode 0600, mtime 21:27:13 МСК; MultiTool PID 617082 запущен после установки в 21:27:13 МСК |
| Проверенные тесты кандидата | 89/89 Node, 1128/1128 Python, по отчёту MT №80 |
| Привязка | `sessions[task_id]=ses_e5ed4f014bcffeNUFuI742WTjLgm`, каталог `/home/kali/Documents/MultiTool/HomeChats/Chat-45` |
| Доказательство маршрута | Новые записи `route_durable_binding` и `route_session_directory` указывают на Chat-45 |
| Сообщение №81 | Подтверждено в Chat-45 и строкой лога `18:45:59.750Z delivered … seq=81` |
| Курсор после доставки | `seen=81`, `inflight` пуст; в логе нет повторных `delivered` для того же seq |
| Кнопка «Вернуть ход» | **Протестирована:** событие №83 `kind=return` отозвало предыдущий отчёт №82, но не удалило историю и не вызвало `session.prompt` |
| Отчёт-замена | MT отправил №84, `mt_p325_final_install_status_audit_report_20261103_v2`; ход вернулся ChatGPT |

MT сообщил о резервных копиях `mt-git-inbox.mjs.bak-pre-release-20261103-212713` и `state.json.bak-pre-release-20261103-212713`. На момент предыдущего отчёта №80 установка действительно ещё не выполнялась; эти сведения теперь **исторические**.

**Итог по отчёту MT №84:** установка, закрепление Chat-45, подтверждение доставки и тест кнопки «Вернуть ход» прошли. Git-событие №83 зафиксировано как `return`, возвращаемый отчёт №82 отмечен `returned=true`, `awaiting_replacement` был установлен, а MT отправил замену №84. Inbox при обработке возврата сообщил `tick_ok dispatched=0 held=0 cleared=0` без новых `inflight` и без повторного запуска задания. Тест ограничен этим диалогом и не доказывает работу на всех возможных сбоях; некритичные ошибки загрузчика и широкая трактовка 404 остаются в backlog.

## Если понадобится переустановка или откат (не выполнять при исправной работе)

**Релиз уже установлен.** Следующие команды сохранены только как аварийная процедура после явного решения владельца. Прежде всего проверить SHA установленного файла, курсор и состояние MT, не запускать скрипт повторно без причины. Для согласованной переустановки полностью завершить MultiTool и проверить наличие резервных копий:

~~~bash
pgrep -af 'multitool-desktop-linux-x86_64.AppImage' || echo 'MultiTool stopped'
ls -l /home/kali/.local/share/mt-release/install-release.sh
bash /home/kali/.local/share/mt-release/install-release.sh
nohup /opt/MultiTool/multitool-desktop-linux-x86_64.AppImage >/tmp/mt-restart.log 2>&1 &
tail -n 30 /home/kali/.local/state/mt-git-inbox/inbox.log
~~~

При повторном использовании сценарий должен **отказаться**, если MT ещё работает или для этой задачи существует inflight. Безопасная установка сохраняет резервные копии 0600, устанавливает проверенный Git SHA и **атомарно меняет только** sessions[task_id] на Chat-45, не трогая seen, inflight или чужие привязки. Если сценария нет, SHA не сходится либо он не прошёл проверку владельца — **не запускать**.

Приёмка: после перезапуска plugin loaded; новая тестовая задача появляется один раз **только** в Chat-45; правильный workspace; seen вырос до seq; inflight очищен; ответ MT находится в той же Git-ветке; кнопка «Вернуть ход» работает без повторного исполнения сообщения MT.

Откат: вернуть предыдущий файл плагина из сохранённого бэкапа только при остановленном MT; при необходимости вернуть **один ключ** sessions[task_id] на предыдущий ID. **Никогда не восстанавливать старую копию всего state.json поверх нового состояния**: это откатит seen и может дублировать поручения.

## Известная авария P33: Hermes Bridge не запускался после краша

Отдельный Git-диалог `task_58deb02895d43f1548bdf265fe118bd8` содержит постмортем MT (сообщения №1 и №3). Мост `hermes-bridge.service` несколько раз не стартовал, порт 8765 не слушал, хотя панель 8787 работала. На диске оставались **осиротевшие** Unix-сокеты `authority-cg.sock` и `authority-mt.sock`. Проверка существования пути в `src/git_dialog/unix_authority.py` отклоняла запуск; `gateway_lifespan.py` скрывал первопричину за `authority_host_failed`, а systemd использовал `Restart=no`.

MT сообщил, что после проверки отсутствия живого владельца сокетов вручную освободил именно осиротевшие пути и поднял сервис; после этого `/ping` и `dialog_list` работали. Проверка кода для безопасного **автоматического** устранения этой ситуации выполнена, но сам патч к моменту отчёта P33 №3 ещё не был установлен. Production HEAD моста на тот момент `4b309cbeeffc7c090f5fc40155062ecdba81bb03`, не путать с релизом MT-плагина 896e189.

**При повторении:** сначала проверить `systemctl --user status hermes-bridge.service`, `ss -xlp`, `ss -ltnp` и метаданные Unix sockets. **Не удалять их автоматически**: путь может принадлежать живому слушателю, а между проверкой и удалением возможна гонка. Любые изменения — только при доказанной осиротелости и с контролем владельца, типа и inode; при сомнении остановиться. Не скрывать эту причину под «сломался Git».

## Диагностика

- Ошибка вызова MCP: сначала read-only dialog_view, проверить waiting_for/head и фактическое наличие записи; platform_blocked ещё не доказывает, что Git не работает.
- Git-сообщение есть, MT молчит: проверить процесс, inbox.log, inflight, seen, каталог сессии. Не сбрасывать курсор автоматически.
- Сообщения в чужом чате: сравнить живую версию плагина, дату старта процесса и sessions[task_id]. Не менять состояние во время session.prompt.
- Четыре plugin loaded при старте хоста не означают четырёх доставок: общий lock сериализует их. Повторяющиеся предупреждения host loader о path пока в backlog.
- Uncertain/404: не реконструировать сессию вслепую. Слабость слишком широкой проверки 404 оставлена в backlog, но код дополнительно проверяет факт отсутствия сессии.

Для восстановления после полного сбоя использовать [RECOVERY_PROMPT.md](RECOVERY_PROMPT.md). Копию локального state, БД и секретов нужно хранить отдельно в защищённом резервном хранилище, не в GitHub.

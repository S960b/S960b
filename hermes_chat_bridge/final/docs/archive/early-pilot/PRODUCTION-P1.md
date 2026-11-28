# P1: отдельный аутентифицированный read-only sidecar

Это первый шаг production-интеграции по прямой команде владельца, а не замена
`localhost:8787`. Код пишет ChatGPT, **устанавливает и испытывает владелец**.
Автоматическое поручение MT на установку не создаётся.

## Что поставляется

- Waitress3.0.2 + WSGI-приложение, жёсткая привязка к127.0.0.1, default8789;
  порт8787/привилегированные порты запрещены. Наружу/Funnel/LAN не публиковать.
- Новая отдельная директория0700, `panel.sqlite`0600 с хешем пароля scrypt
  (N32768/r8/p1/32байта результата/случайная32байта соль). Пароль не в argv/env/Git.
- Пользователь `owner`, настоящий HTTP Basic challenge браузера. Это не OAuth
  и не фиктивный флаг owner в JSON. Доступ закрыт и для HTML, и для JSON/status.
  Сравнение хешей constant-time; после5 неудач за60s временная блокировка.
- Один локальный процесс-authority на state-directory, exclusive flock.
  В P1 эта authority обслуживает только чтение. **Все старые Git writers ещё
  не переведены на единый write-gateway.** Это отдельный следующий этап.
- Синтетический источник по умолчанию; реальная беседа включается отдельно,
  только существующая `dialogs/task_3c2d37a2485b` в PRIVATE S960b/agent-dialogs.
- Реальный polling default60s (допустимо30..3600), только GET, проверка private,
  main-checkpoint, pinned commit/tree/blob/hash/истории. Неизменившийся HEAD
  не перечитывает все blobs. Никаких POST/PATCH/create/delete refs.
- При пропавшей ветке/403/изменённой истории/другом main — ошибка источника,
  HTML503, аутентифицированный status содержит машинный код; старая история
  не выдаётся за свежую и synthetic fallback не происходит.
- `/bridge/status` — аутентифицированная **пассивная интеграционная точка**:
  тот же task_id/head/mode/error/read_only. Мост остаётся wakeup/резервным каналом;
  P1 не читает/не меняет его БД, не вызывает bridge_put_*, не меняет подписки.
  Автоматическое подключение старого моста к этому endpoint пока не выполнено.
- Нет записи, исполнения задач, подтверждения, закрытия или удаления через HTTP.
  Delete disabled. GET HTML обновляется через15s; история пагинирована/экранирована.

Это локальный сервис для первого испытания, **не готовая публичная Internet-edge
установка**. Basic по HTTP разрешён только на loopback. Для удалённого доступа
нужен отдельно проверенный TLS/SSH-контур; сейчас их не настраиваем. Browser Basic
credentials кешируются браузером; для тестов используйте отдельное приватное окно.

## Установка в НОВУЮ копию (не в каталоги старых служб)

Точный SHA P1 сообщён в ответе ChatGPT. Ниже переменная `p1_sha` должна содержать
этот SHA, а не подвижную ветку. `p1_dir` — новый каталог; команд удаления нет.

```sh
p1_dir="$(mktemp -d "$HOME/git-dialog-p1.XXXXXXXX")"
git clone --single-branch --branch implementation/git-dialog-v1 git@github.com:S960b/agent-dialogs.git "$p1_dir/code"
cd "$p1_dir/code"
git checkout --detach "$p1_sha"
git rev-parse HEAD
python3 -m venv .venv-p1
.venv-p1/bin/python -m pip install -r requirements-service.txt pytest==9.1.1
PYTHONPATH=src .venv-p1/bin/python -m pytest tests -q
.venv-p1/bin/python -m compileall -q src
```

Ожидается394 теста. Сеть в pytest запрещена, SQLite толькоtmp_path. Waitress
нужен и для одного теста wiring; зависимости ставятся только в новую `.venv-p1`.
Если в Kali недоступен модульvenv/pip — сначала устраните это стандартным способом
на своей машине; не используйте окружение hermes-taskboard. Никакого sudo в этих
командах, systemd unit/cron/daemon не устанавливаются.

## Первый запуск: только synthetic

В том же терминале, из корня новой копии:

```sh
PYTHONPATH=src .venv-p1/bin/python -m git_dialog.service init --state-dir "$p1_dir/state"
```

Введите новый отдельный пароль дважды (минимум14байт, максимум256; не ваш PAT).
Терминал скрывает ввод; без интерактивного терминала init запрещён. Новая БД
`$p1_dir/state/panel.sqlite`, логин `owner`. Повторный init на существующем каталоге
останавливается, ничего не перезаписывает. Пароль не присылайте в чат/мост.

```sh
PYTHONPATH=src .venv-p1/bin/python -m git_dialog.service serve --state-dir "$p1_dir/state" --port 8789
```

Откройте **http://127.0.0.1:8789/**, введите `owner` и новый пароль в окне браузера.
Ожидается синтетическая `task_preview_fixture`, не настоящая история. Без входа401,
после входа200, кнопка удаления disabled. На `/api/status` и `/bridge/status`
ожидается mode=synthetic/read_only=true/delete_enabled=false.
Для проверки без браузера в другом терминале (пароль curl запросит скрыто):

```sh
curl -i http://127.0.0.1:8789/api/status
curl --user owner http://127.0.0.1:8789/bridge/status
```

Первый запрос401 — это ожидаемая защита, не сбой установки. Не вводите пароль
как `owner:пароль` в команду. Неверный Host/Origin/peer —403, любые методы записи
после аутентификации —405. Если8789 занят, процесс не убивает слушателя: выберите
другой свободный непривилегированный порт, кроме8787, и соответствующий URL.

## Явное включение read-only реальной беседы

Остановите ТОЛЬКО новый foreground-процесс Ctrl+C. В том же терминале:

```sh
PYTHONPATH=src .venv-p1/bin/python -m git_dialog.service serve --state-dir "$p1_dir/state" --port 8789 --mode github --poll-seconds 60
```

Терминал скрыто спросит **локальный путь к уже настроенному PAT-файлу**. Укажите
его только там; не вставляйте в чат/мост/команды/логи/Git. Код сам читает только
этот файл0600/currentUID/regular/no-final-symlink. Значение PAT не вводится в форму
логина и не пишется в новую БД. Путь не сохраняется; при restart вводится заново.
Для неинтерактивного systemd-запуска конфигурация ещё не реализована — не обходить
проверку терминала и не копировать секреты в unit/env. Права/срок PAT не менять.

До открытия listener код выполнит один read-only preflight. Если авторизация
или checkpoint неверны, старт завершится машинной ошибкой, не покажет synthetic
как будто это Git. После старта HTML содержит реальную task_3c2d37a2485b,
status mode=github/head/last_checked_utc. Интервал60s, HTML refresh15s; сетевой
сбой не даёт новый execution/send grant. Main не меняется; тела не исполняются.

## Остановка и откат первого шага

Ctrl+C останавливает только новый foreground-сервис; HTTP-сокет закрывается,
poller получает stop и ограниченное ожидание завершения текущих GET. Старый8787
должен остаться неизменным. БД/новую копию не удалять: можно просто не запускать
sidecar. Возврат к synthetic — та же командаserve без `--mode github`.

Для отчёта достаточно exactSHA/Python/pytest/summary, HTTP401→200, mode/status/head,
код ошибки если есть, сохранность8787 и факт остановки нового процесса. Не слать
пароль/Authorization/PAT/его путь/дампы БД. `source_unavailable` смотрите через
аутентифицированный `/api/status`; не лечить force/refcreate/сбросом БД.

## Что остаётся следующим этапом

Подключение read-only sidecar к фактическому wakeup-пути старого моста и общему
write-gateway, роли агентов, реальный пользовательский confirmation/delete и
перевод всех writers требуют отдельной реализации и проверки. **Ничего из этого
не считается установленным данным P1.** Замена8787 — только после вашей приёмки
отдельным решением. Службыhermes-taskboard/hermes-bridge, OAuth, подписки, cron,
старые БД/задачи/истории здесь не меняются.

## Основания и проверка

- Waitress используется вместо тестового stdlib HTTPServer:
  https://docs.pylonsproject.org/projects/waitress/en/stable/
- Настройки bind/limits/timeouts:
  https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html
- scrypt API:
  https://docs.python.org/3.13/library/hashlib.html#hashlib.scrypt

Offline service-тесты и отдельный bounded Waitress HTTP-smoke используют только
новую временную БД и synthetic данные. Результаты owner-машины ещё ожидаются;
не путать код/локальные тесты ChatGPT с вашим фактическим развёртыванием.

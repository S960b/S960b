# Stage 38D — завершающая архивация claim-intent seq=5

## Предпосылки

ChatGPT через pg **уже** подтвердил в PostgreSQL:

- task `task_b6c26aa18220412bafdbf32393f16568`, version=6, last_seq=6, `next_actor=chatgpt`;
- seq=5 `delivered`, delivery_id=21, attempts=1;
- seq=6 `delivered`, delivery_id=22, attempts=1;
- ChatGPT `managed_ack_report(seq=6, message_id=pgv2-mg-b6c26aa18220412bafdbf32393f16568-5-report)` дал `acked=true`;
- `dispatch_enabled=false`, `policy_version=4`, `write_permit_present=false`.

Агентская роль (`bridge_agent`) намеренно **не может** проверить `delivery(seq=6)` и возвращает `managed_actor_unavailable` для сообщений направления agent→ChatGPT. Поэтому этот пакет **не пытается** запрашивать её под чужой ролью, не меняет права и явно использует закреплённую операторскую аттестацию `operator_pg_verified_ack38d.json`. Она фиксирует **ранее проверенное ChatGPT состояние**, а не выдаёт его за независимую проверку со стороны агента. Из файла нельзя вывести секретные ключи.

## Что делает

1. Проверяет собственные контрольные суммы, точные SHA исходников Stage38C и Stage38;
2. проверяет приватную завершённую квитанцию `one-shot-receipt.json` (`seq5_ack_confirmed`);
3. проверяет средствами **агентской** роли (`status`, `delivery(5)`, `peek`) что отчёт существует с точным текстом/ID, seq5 доставлен и диалог на seq6 в ожидании ChatGPT;
4. проверяет аттестацию ChatGPT, точное состояние config, watchdog, локального курсора/inflight и отсутствие permit/gate;
5. **только после всех проверок** атомарно перемещает один блокирующий журнал из `claim-intents37b2c/<task_id>.json` в `~/hermes-pg-stage38c-manual-recovery/archived-intent-seq5-after-ack.json`, fsync обоих каталогов.

Пакет **не вызывает** SQL-операции записи, не включает gate/permit, не запускает SDK, не меняет `opencode.json`, MultiTool и MCP, не пересоздаёт задания/отчёты и не меняет dispatch.

## Выполнение

Архив `hermes-pg-stage38d-finalize-after-ack.zip` положить в `/home/kali/Downloads`. Проверить SHA256 внешнего архива, затем распаковать в приватный каталог. Выполнить `bash <unpacked>/stage38d/540_finalize_historical_ack_once.sh` **один раз**. При STOP не повторять, вернуть маркеры.

`sha256sum -c SHA256SUMS` проверяет весь состав. Скрипт использует `python3 -IB`, но только абсолютные импорты через `importlib.util.spec_from_file_location` (не полагается на `sys.path`).

Если `STAGE38D_FINALIZATION_COMPLETE_NEXT_TASK_NEEDS_SCOPED_WINDOW` выдан, старый журнал архивирован; следующий рабочий сеанс по-прежнему требует новых scoped-permissions. Это не повторное включение постоянной записи.

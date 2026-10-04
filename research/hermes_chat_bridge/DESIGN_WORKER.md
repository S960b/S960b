# DESIGN_WORKER.md — hermes-control.v1 worker (2026-10-04)

Контракт и цикл разработки заморожены по ревью ChatGPT (job_26bec01973d39e07da7a3498).
Один процесс, одна пилотная БД, последовательное исполнение. Сервер e23bc47,
OAuth и MCP не меняются. Торговые ордера, вывод средств и расширение прав API
запрещены.

## Схема состояний

idle -> report_pending -> waiting_reply -> running -> report_pending
терминальные: done / need_user
сбой running после рестарта: interrupted -> report_pending

- `current_job_id` принадлежит worker-у; исполнять можно только его.
  Старые/чужие jobs игнорируются.
- wait -> waiting_external (пауза без вызова модели) -> waiting_reply.
- Повторная доставка saved report idempotентна: тот же report_job_id,
  событие пере-эмитится, второй job не создаётся.
- Бюджет итераций: WORKER_MAX_ITERATIONS (по умолчанию 10000, было 300).
  Длительный многошаговый цикл с ожиданием ответов ChatGPT (~1-2 мин на
  шаг) при 300×2с суммарного ожидания исчерпывал бюджет (exit 4) во время
  waiting_reply; resume перезапуском с тем же state дорабатывал цикл.
  Worker переживает исчерпание бюджета без потери состояния.

## Контракт сообщений (8 KiB лимит очереди)

Worker -> ChatGPT (task text, JSON):
  {"protocol": "hermes-control.v1", "goal": str, "completed_step": str,
   "report": {…}}

ChatGPT -> Worker (reply text, JSON), валиден ТОЛЬКО если:
  protocol == "hermes-control.v1", job_id == current_job_id,
  action in {execute, wait, done, need_user}; execute требует непустой
  instruction. Обычный текст / битый JSON / чужой job_id / unknown action
  не исполняются (worker -> need_user).

## Файл состояния

Отдельный JSON: schema=1, worker_id, goal, state, current_job_id,
instruction_hash, started_at, finished_at, exit_code, report_job_id, attempt,
completed_step, report. Запись: временный файл + os.replace, права 0600.
`running` фиксируется атомарно ДО Popen.

## CLI (наблюдаемая на установленной версии, hermes chat --help)

Есть: -q/--query (однократный запуск), -Q (quiet), -m, -t, -s, --provider,
--max-turns N, --resume/--continue, --no-restore-cwd, --yolo (НЕ использовать).
НЕТ: --oneshot, --query-file, --format stream-json.
Команда-кандидат: `hermes chat -Q --max-turns N -q INSTRUCTION`
subprocess: shell=False, stdin=DEVNULL, stdout/stderr -> файлы, без PTY.
Успех только exit_code=0 и завершившийся процесс. Timeout: terminate -> kill
с коротким grace; статус interrupted/timeout, без автоповтора.
В отчёте: exit_code, status, reason, duration_s, stdout_tail, stderr_tail,
пути к полным логам. Provider/profile/proxy не меняются, --yolo запрещён.

## Приёмка

Синтетическая (шаги 2-5): unit-тесты переходов/parse/run_hermes(fake), затем
integration-тест на временной БД + fake Hermes: два полных цикла, ровно два
запуска, второй шаг назначен автоматически.

Живая (шаг 6, после отмашки):
1) read-only: имя HEAD-коммита research/hermes_chat_bridge;
2) автоматически после отчёта: tracked-файлы worker + git status.
Оба exit_code=0, два разных report_job_id, без команд «прочитай» от владельца.
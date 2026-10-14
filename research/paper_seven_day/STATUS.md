# Paper Seven-Day - Status & Recovery (2026-10-14)

**Branch:** `chatgpt/paper-seven-day-2026-10-08` (11 commits ahead of `main`)

Это живой статус экспериментального 7-дневного paper-раннера (SOLUSDT/BTCUSDT,
60m Donchian, no-order, GET-only, виртуальная торговля). Файл обновляется по
ходу отладки. **Ветка НЕ влита в main** - это незавершённая работа.

## Что это

- `research/paper_seven_day/` - скрипты (risk_paper_runner.py, seven_day_exec.py,
  paper_checkpoint.py + исследования: sol_walkforward_365, regime_filter_study,
  reversal_study, compression_breakout, funding_screen, and more).
- Live-копии живут в `/home/kali/safetrade-research/scripts/` (риск-раннер
  ревизии 2-6, финальная = job_b1d19807, 64 проверки).
- Отдельный git-репозиторий `/home/kali/safetrade-research/` (не S960b) - там
  вся история правок risk_paper_runner.py.

## Инфраструктура (systemd user + crontab)

- `paper-seven-day.service` - user unit, ExecStart через flock + venv python,
  запускает `seven_day_exec.py` (persistent 7-day deadline wrapper) -> `risk_paper_runner.py`.
- `paper-checkpoint.service` + `paper-checkpoint.timer` - каждый день 21:00 МСК,
  архив чекпоинта + отчёт в bridge.
- crontab (user): `45 14 * * *` send_lead_lag_1445.py, `*/3` bridge_poller.py,
  `34 10 8 10 *` t12_checkpoint.py.

## Текущее состояние - 2026-10-14 14:40 МСК

- **Сервис РАБОТАЕТ** (active running) с 14:23 МСК после мануального запуска.
- **Дедлайн забега:** 2026-10-15 23:56 МСК (осталось ~33 ч).
- **БД `risk_paper.db`:** eq1=101.75, eq05=100.62, открытых позиций нет,
  3 трейда (SOLUSDT +3.77, BTCUSDT -1.13, BTCUSDT -0.9 по стопу).
- heartbeat живой: `last_heartbeat_utc`, `last_success_BTCUSDT/SOLUSDT` - свежие,
  loop_count растёт.

### Что было сломано и как починено (2026-10-12/14)

- **Симптом:** сервис упал 12.10 07:05 с `exit 69`, юнит в failed,
  `flock: failed to execute /tmp/bridge-clean-venv/bin/python: No such file or directory`.
- **Корень:** венирон `/tmp/bridge-clean-venv` пропал после ребута 11.10 23:55
  (каталог `/tmp` очищается при перезагрузке). На него завязаны:
  - `paper-seven-day.service`, `paper-checkpoint.service` (systemd user),
  - crontab: `bridge_poller.py`, `send_lead_lag_1445.py`, `t12_checkpoint.py`,
  - скрипты: `daily_paper_checkpoint_report.py` (PY=), `t12_checkpoint.py` (BRIDGE_PUT=).
- **Временный фикс (сделан):** создал `/tmp/bridge-clean-venv` через
  `python3 -m venv` (скрипты stdlib-only, пакеты не нужны), запустил сервис - работает.
- **Кардинальный фикс (НЕ сделан, ждёт GO):** перенести venv на постоянный путь
  `~/.venvs/bridge-clean` и обновить ВСЕ ссылки (systemd-юниты, crontab, PY= в
  скриптах). Команды:
  ```bash
  python3 -m venv /home/kali/.venvs/bridge-clean
  sed -i 's#/tmp/bridge-clean-venv#/home/kali/.venvs/bridge-clean#g' \
    /home/kali/.config/systemd/user/paper-seven-day.service \
    /home/kali/.config/systemd/user/paper-checkpoint.service
  systemctl --user daemon-reload && systemctl --user restart paper-seven-day
  # + sed в crontab и в PY= внутри /home/kali/.local/state/hermes-bridge/*.py,
  #   /home/kali/safetrade-research/scripts/t12_checkpoint.py,
  #   /home/kali/safetrade-research/scripts/daily_paper_checkpoint_report.py
  ```

## Заметки

- Скрипты paper - **чистый stdlib** (sqlite3, urllib, argparse и т.д.), пакетов не требуют.
- `daily_paper_checkpoint_report.py` шлёт отчёт через `bridge_cli.py`
  (`/home/kali/S960b/research/hermes_chat_bridge/bridge_cli.py`) в pilot БД моста -
  зависит от живого моста.
- Ветка `chatgpt/paper-seven-day-2026-10-08` - это "накопитель" экспериментальных
  исследований (safetrade + ctf), не влита в main. Вливать только после
  согласования и желательно вынести ctf-наработки отдельно.
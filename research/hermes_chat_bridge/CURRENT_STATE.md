# Hermes - CURRENT STATE (передача контекста между сессиями)

Обновлён: 2026-10-15 (вечер). Читается за ~1 минуту. Все подробности - в памяти, скиллах и git (ссылки внизу).

## 0. САМОЕ ВАЖНОЕ (незакрытый хвост)

1. **МОСТ РАБОТАЕТ НА ПОСТОЯННОМ HTTPS-АДРЕСЕ ЧЕРЕЗ TAILSCALE FUNNEL:**
   `https://kali.tail12eeb2.ts.net` -> `http://127.0.0.1:8765` (MCP endpoint `/mcp`).
   BRIDGE_BASE в `pilot_environment.private.json` = `https://kali.tail12eeb2.ts.net`,
   OAuth issuer отдаётся с новым адресом, /ping 200, /mcp без токена = 401 (auth работает).
   `current_tunnel_url.txt` = `https://kali.tail12eeb2.ts.net` (таск-борд показывает его).
2. **Старые Cloudflare-туннели ПОЛНОСТЬЮ УДАЛЕНЫ (2026-10-15)**: quick-туннель,
   named-туннель и автосинк-таймер `hermes-tunnel-url.timer` отключены, unit-файлы удалены
   (и из git тоже). Логи cloudflared удалены. Осталась только запись в истории git
   (systemd/*.service, update_tunnel_url.sh) - можно посмотреть, не используется.
3. **Tailscale**: нода `kali` (100.102.200.90) в tailnet `S960b`, FQDN `kali.tail12eeb2.ts.net`,
   Funnel на 443 -> 127.0.0.1:8765. `tailscaled.service` - системный, **нужно проверить,
   что включён автозапуск** (`sudo systemctl enable --now tailscaled`), иначе после ребута
   адрес умрёт. Funnel переживает ребут tailscaled (конфиг хранится в tailnet).
4. **is-a.dev домен - МЁРТВАЯ ВЕТКА, НЕ ИСПОЛЬЗОВАТЬ**: их CI запрещает CNAME на
   `.cfargotunnel.com` (проверено тестами `disallowed-cnames.json`). PR #56644 закрыт ботом.

## 1. Cервисы (все active, автозапуск через systemd user + Linger)

- `hermes-bridge.service` - MCP-мост Hermes<->ChatGPT, порт 8765.
- `hermes-taskboard.service` - таск-борд, порт 8787 (localhost).
- `paper-seven-day.service` - 7-дневный paper-забег (SOL/BTC).
- `tailscaled.service` (системный) - нода `kali` в tailnet `S960b`, Funnel.
  **ПРОВЕРИТЬ автозапуск**: `systemctl is-enabled tailscaled` -> enabled
  (иначе после ребута мост останется только на localhost).

## 2. Важные среды/файлы (секреты НЕ печатать)

- Venv: `~/.venvs/bridge-clean` (НЕ /tmp! - там был баг с ребутом).
- Env моста: `/home/kali/.local/state/hermes-bridge/pilot-20261004T140717Z/pilot_environment.private.json`
  (BRIDGE_DB_PATH, BRIDGE_BASE, BRIDGE_PASS - секрет).
- БД моста: там же `pilot.db` (jobs, subs, clients, access_tokens).
- БД paper: `/home/kali/safetrade-research/data/risk_paper.db`.
- Таск-борд: `/home/kali/S960b/research/hermes_chat_bridge/taskboard.py`.

## 3. Незакрытые хвосты

1. **Tailscale автозапуск** (см. п.0): `sudo systemctl enable --now tailscaled` - проверить,
   что после ребута адрес жив.
2. **Paper-забег**: дедлайн 2026-10-15 23:56 МСК. После финиша собрать итоговый отчёт по чек-листу
   ChatGPT (в памяти/струях: A-F: equity, maxDD, reconcile, MTM позиций, раздельные выводы, НЕ переходить
   в live, новый uninterrupted run). Статус валидности: INTERRUPTED (~57% аптайма). Сервис active.
3. LinkedIn: токен истекает ~2026-11-22, ре-авторизация ~17.11 (крон-напоминание стоит). Постинг 20:00 МСК.

## 4. Где лежит ВСЁ остальное (подробности)

- **Память Hermes**: факты о LinkedIn, мосте, paper, правила (пуш только в main, PII-маскирование,
  ad-hoc верификация после правок кода, etc).
- **Скиллы** (в ~/.hermes/skills/):
  - `hermes-chatgpt-bridge-ops` - операции с мостом (архитектура, complete_to_hermes, OAuth TTL,
    туннель/BRIDGE_BASE автосинк, label-префиксы job_id).
  - `hermes-chatgpt-bridge-jobs` - исполнение заданий ChatGPT (руки/голова, отчёты).
  - `linkedin-content-research` - цикл постинга LinkedIn (контракт юзера).
  - `ctf-writeup-repo` - конвенции репозитория S960b (маскирование, проверки).
- **Git S960b** (main, HEAD bff148f):
  - `linkedin/` - вся документация постинга.
  - `research/hermes_chat_bridge/` - код моста, таск-борд, systemd-юниты (в `systemd/`).
  - `research/paper_seven_day/` - скрипты paper + STATUS.md.
  - Веток прочих нет (все влиты/удалены). Только main.

## 5. Как продолжить в новой сессии

Просто скажи: "ознакомься с research/hermes_chat_bridge/CURRENT_STATE.md и продолжай".
Агент прочитает этот файл (маленький) и подхватит. Проверка постоянного адреса:
`curl -s https://kali.tail12eeb2.ts.net/ping` (должен вернуть `{"ok":"ok",...}`) и
`tailscale funnel status` (Funnel on, proxy -> 127.0.0.1:8765).
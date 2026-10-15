# Hermes - CURRENT STATE (передача контекста между сессиями)

Обновлён: 2026-10-15 (вечер). Читается за ~1 минуту. Все подробности - в памяти, скиллах и git (ссылки внизу).

## 0. САМОЕ ВАЖНОЕ (незакрытый хвост)

1. **Именованный Cloudflare-туннель создан и РАБОТАЕТ** (id `5891694c-8db9-49e4-97be-ab9ee449e799`),
   сервис `hermes-tunnel-named.service` (active). Ведёт `s960b-bridge.is-a.dev -> http://127.0.0.1:8765`.
   Токен - секрет: `/home/kali/.cloudflared/hermes-bridge.token` (0600, не в git).
2. **Домен `s960b-bridge.is-a.dev` - PR НЕ СМЕРЖЕН, ЗАКРЫТ БОТОМ (ПРОВЕРЕНО 2026-10-15).**
   Причина: PR #56644 в is-a.dev/register был **closed, not merged** - бот `is-a-dev-bot` отклонил
   за НЕЗАПОЛНЕННЫЙ шаблон ("Incomplete PR template"). Файл форка верный
   (`S960b/register/main`, `domains/s/s960b-bridge.json`, records CNAME ->
   `5891694c-8db9-49e4-97be-ab9ee449e799.cfargotunnel.com`), но в upstream**`domains/s/s960b-bridge.json` = 404**.
   DNS: `dig CNAME` пусто, `A` = 104.18.4.103/5.103 (CF-плейсхолдер is-a.dev, НЕ наш туннель).
   Вывод: переключение на постоянный адрес СЕЙЧАС НЕВОЗМОЖНО - домен не зарезолвлен.
   Нужно ПЕРЕОТКРЫТЬ PR с полностью заполненным шаблоном (чекбоксы TOS/структура/reachable/
   software/non-commercial/contact/website_link + website preview/purpose). GH CLI/токена нет,
   есть только SSH-ключ -> создание PR через API невозможно, нужна пользовательская сессия.
3. **Пока работает старый quick-туннель**: `attribute-appliance-hotel-regardless.trycloudflare.com`
   (BRIDGE_BASE и таск-борд указывают на него). ChatGPT ходит на него, всё работает.
4. **После мержа PR** (когда DNS отдаст CNAME на cfargotunnel) сделать:
   - `BRIDGE_BASE` -> `https://s960b-bridge.is-a.dev` (обновится автоматически нашим таймером, но проверить);
   - `systemctl --user disable --now hermes-tunnel.service` (выключить старый quick-туннель);
   - проверить `curl https://s960b-bridge.is-a.dev/ping` -> 200, OAuth discovery, POST /mcp;
   - дать юзеру постоянный адрес `https://s960b-bridge.is-a.dev/mcp` (вписать в плагин ChatGPT);
   - обновить `current_tunnel_url.txt` файл (таск-борд показывает его) - тоже на новый адрес.

## 1. Cервисы (все active, автозапуск через systemd user + Linger)

- `hermes-bridge.service` - MCP-мост Hermes<->ChatGPT, порт 8765.
- `hermes-tunnel.service` - СТАРЫЙ quick-туннель (временный, выключить после мержа домена).
- `hermes-tunnel-named.service` - НОВЫЙ именованный туннель (config: `~/.cloudflared/config.yml`).
- `hermes-tunnel-url.timer` - каждые 60с: синк URL + BRIDGE_BASE + рестарт моста при смене.
- `hermes-taskboard.service` - таск-борд, порт 8787 (localhost).
- `paper-seven-day.service` - 7-дневный paper-забег (SOL/BTC).

## 2. Важные среды/файлы (секреты НЕ печатать)

- Venv: `~/.venvs/bridge-clean` (НЕ /tmp! - там был баг с ребутом).
- Env моста: `/home/kali/.local/state/hermes-bridge/pilot-20261004T140717Z/pilot_environment.private.json`
  (BRIDGE_DB_PATH, BRIDGE_BASE, BRIDGE_PASS - секрет).
- БД моста: там же `pilot.db` (jobs, subs, clients, access_tokens).
- БД paper: `/home/kali/safetrade-research/data/risk_paper.db`.
- Таск-борд: `/home/kali/S960b/research/hermes_chat_bridge/taskboard.py`.

## 3. Незакрытые хвосты

1. **Домен** (см. п.0) - ждём мержа PR, потом переключение.
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
Агент прочитает этот файл (маленький) и подхватит. Если хвост по домену уже закрыт -
начни с проверки DNS: `dig +short s960b-bridge.is-a.dev CNAME` (должен вернуть <id>.cfargotunnel.com).
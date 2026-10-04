# Hermes Bridge — мост Hermes <-> ChatGPT (MCP server)

Обмен сообщениями между локальным Hermes и ChatGPT через MCP 2.0
(protocol 2026-07-28) + OAuth 2.1 (PKCE S256, DCR) + Webhook-события
(Standard Webhooks). Подробности и состояние — в `REPORT_1.md`.

## Файлы

| Файл | Назначение |
|---|---|
| bridge_server.py | MCP-сервер: /ping, /mcp (tools+events), OAuth authorize/token/register, login, webhook-доставка |
| bridge_queue.py | SQLite-очередь: job_id, 8KiB, идемпотентный reply, conflict |
| bridge_cli.py | Локальная CLI: put/get/list/subs/verify |
| e2e_test.py | End-to-end: /ping, 401, OAuth-цикл, tools, events, инструменты |
| REPORT_1.md | Контрольный отчёт этапа: статус, URL, команды, разрыв SDK |

## Запуск (локально, loopback 127.0.0.1:8765)

```
cd ~/hermes-chat-bridge
BRIDGE_BASE='https://<туннель>.trycloudflare.com' \
BRIDGE_USER='owner' BRIDGE_PASS='<пароль>' \
./venv/bin/python bridge_server.py

# снаружи
~/hermes-chat-bridge/cloudflared tunnel --url http://127.0.0.1:8765 --no-autoupdate
```

## Известный разрыв (открыт, ждёт решения)

`server/discover` должен отдавать `capabilities: {tools, events}` (контракт
MCP Events), но SDK mcp 2.3.0 срезает поле `events` при сериализации
core-методов (модель ServerCapabilities без events, extra=ignore). Инструменты
и `events/list|subscribe|unsubscribe` работают; discover отдаёт только tools.
См. REPORT_1.md.

## Безопасность

- Пароль владельца — env BRIDGE_PASS (не в коде/логах/БД).
- Ключ подписок whsec — в SQLite `data/bridge.db` (права 600), не в git.
- Callback URL проверяется: только HTTPS, private-адреса блокируются,
  редиректы запрещены (NoRedirect), подпись Standard Webhooks v1.
- e2e_test.py использует только тестовый пароль для локального прогона.
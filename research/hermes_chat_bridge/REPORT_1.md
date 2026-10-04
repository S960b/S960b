# Hermes Bridge — контрольный отчёт этапа (по плану hermes_chat_bridge_task.md)

Дата: 2026-10-04 14:35 МСК. Время работы этапа: ~1.5 ч.

## Готово и проверено (локально + через публичный туннель)

**Публичный URL:** `https://persons-anymore-discussing-pushed.trycloudflare.com`
(Quick Tunnel; URL меняется при перезапуске туннеля — план допускает это,
строка 50). Сервер слушает loopback 127.0.0.1:8765, снаружи — Cloudflare.

| Проверка | Результат |
|---|---|
| GET /ping (публичный, без данных) | 200 `{"ok":"ok","version":"0.1.0"}` |
| POST /mcp без токена | 401 (доступ запрещён) |
| OAuth metadata `/.well-known/oauth-authorization-server` | issuer/authorize/token/registration, PKCE S256, client_secret_post |
| RFC 9728 `/.well-known/oauth-protected-resource/mcp` | resource + authorization_servers, scopes bridge |
| DCR /register → /authorize → /login → /token | полный цикл OK (Bearer + refresh) |
| tools/list | bridge_get_message, bridge_put_reply |
| events/list | hermes.message.created (delivery webhook, фильтр queue) |
| tools/call bridge_get_message (несуществующий job) | `{"status":"not_found"}` |
| Очередь (SQLite): put/get/reply/конфликт | идемпотентный повтор OK; другой ответ → conflict |
| events/subscribe с приватным callback URL | отклонён (CallbackEndpointError), private-адреса блокируются |
| Подпись Standard Webhooks | v1,<b64> — вектор проверен |

## Точный разрыв совместимости SDK (блокер events на server/discover)

Контракт MCP Events (developers.openai.com/plugins/build/mcp-events) требует в
`server/discover` ответе `capabilities: {"tools": {}, "events": {}}` (events на
верхнем уровне). SDK `mcp` 2.3.0 (Python) этого не умеет:

- Модель `ServerCapabilities` в `mcp_types` (используется runner'ом при
  сериализации ответа для `server/discover` при `resultType=complete`) НЕ
  содержит поля `events` и молча срезает его (extra=ignore): на проводе уходит
  только `{"tools": {}}`.
- Прямой возврат словаря через `add_request_handler('server/discover', ...)`
  не помогает: runner прогоняет ответ через `_methods.serialize_server_result`
  для SPEC_CLIENT_METHODS (server/discover входит в список) при `resultType`
  из CORE_RESULT_TYPES. `events/list|subscribe|unsubscribe` при этом работают
  (это не core-методы — сериализуются свободно).
- В официальном python-sdk `events/subscribe` не реализован (поиск по
  репозиторию: 0 результатов).

Итог локального E2E: **11/11 проверок, из них 1 — разрыв SDK** (discover events
capability), остальные зелёные. Инструменты и события list/subscribe работают;
инструменты подключены и доступны после OAuth.

## Команды (воспроизводимость)

```
# сервер (loopback; снаружи — туннель)
cd ~/hermes-chat-bridge
BRIDGE_BASE='https://persons-anymore-discussing-pushed.trycloudflare.com' \
BRIDGE_USER='owner' BRIDGE_PASS='<пароль>' \
./venv/bin/python bridge_server.py

# туннель
~/hermes-chat-bridge/cloudflared tunnel --url http://127.0.0.1:8765 --no-autoupdate

# локальная CLI
./venv/bin/python bridge_cli.py put "тестовая фраза"
./venv/bin/python bridge_cli.py get <job_id>
./venv/bin/python bridge_cli.py verify   # самопроверка подписи
```

Пароль владельца задаётся env BRIDGE_PASS (в БД/логах не хранится), логин demo
owner. Секреты подписок — в SQLite data/bridge.db (права 600), не в git.

## Что осталось по плану

1. **server/discover с events** — требует решения инициатора плана: либо
   дождаться поддержки events в SDK (в python-sdk её пока нет), либо
   низкоуровневый JSON-RPC слой, где discover сериализуется вручную, либо
   расширение surface-модели. Сам обход не изобретаю (инструкция).
2. **Первый полный обмен** (тестовая фраза → ответ из чата ChatGPT → чтение
   в консоли) — не выполнялся: ждём зелёного server/discover и создания
   сервера MCP в UI ChatGPT.
3. Остальные шаги плана (повторная доставка, перезапуск, проверка отказов) —
   после первого обмена.
# Перенос Hermes Chat Bridge из research/

**Дата:** 10 ноября 2026 года.

Причина: мост ChatGPT ↔ локальный MultiTool/Hermes — самостоятельный инфраструктурный проект, а не торговая гипотеза или финансовое исследование. Кроме того, старые README/CURRENT_STATE описывали октябрьскую SQLite-очередь и прежние MCP-методы, тогда как основная поддерживаемая Git-реализация развивается в приватном `S960b/agent-dialogs`.

## Изменение структуры

```text
Было:
research/hermes_chat_bridge/
  bridge_server.py, bridge_queue.py, taskboard.py, README.md, reports/, ...

Стало:
hermes_chat_bridge/
  README.md             — современное объяснение проекта
  CURRENT_STATE.md      — точка входа на 10.11.2026
  MIGRATION.md          — этот документ
  legacy/               — ВСЕ исторические файлы без изменения байтов

research/hermes_chat_bridge -> ../hermes_chat_bridge/legacy
                              (символическая ссылка для совместимости на Kali/Linux)
```

Исторический каталог `legacy/` сохранён целиком без удаления исходников и тестов; его файлы не патчились. Символическая ссылка **не** делает старый SQLite-протокол актуальным, а лишь сохраняет файловый путь для возможных компонентов live MCP-обвязки и исторических ссылок. На платформах, не поддерживающих Git symlink, совместимость этого пути не гарантируется.

## Где настоящая рабочая версия

- [S960b/agent-dialogs](https://github.com/S960b/agent-dialogs) — разработка/код Git-диалогов, authority, Taskboard и MT Inbox.
- [P36.2 release](https://github.com/S960b/agent-dialogs/blob/main/docs/P362_RELEASE_CLOSED_20261108.md) — последняя подтверждённая до этой ревизии приёмка.
- [CHATGPT_GIT_BRIDGE.md](https://github.com/S960b/agent-dialogs/blob/main/CHATGPT_GIT_BRIDGE.md) — протокол новых чатов, обновлённый 6 ноября.

## Ограничения и безопасность

Эта операция — перенос файлов **в Git**, не установочная миграция рабочей Kali. Запущенные процессы, systemd-юниты, Tailscale, ключи, БД, пользовательские чаты и `dialogs/*` не трогали. Перед применением нового checkout следует проверить, не требует ли работающая служба реальный каталог (вместо symlink) или путь в конфигурации. Не удалять `legacy/bridge_server.py`: документация текущего Git-моста ещё ссылается на него как возможный live transport.

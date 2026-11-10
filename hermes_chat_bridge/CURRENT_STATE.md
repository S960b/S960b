# Актуальное состояние проекта — 10 ноября 2026 года

## Главный вывод

**Рабочий протокол — Git-диалоги** в приватном [S960b/agent-dialogs](https://github.com/S960b/agent-dialogs). Эта публичная папка — описание проекта и архив первой очередной реализации, не отдельный production checkout.

В старом `legacy/CURRENT_STATE.md` указан октябрьский срез на 15.10.2026: `job_id`, SQLite-очередь, `bridge_put_*`, OAuth/Tailscale Funnel. Его **нельзя** считать текущим состоянием на ноябрь.

## Откуда берётся подтверждение

- **6 ноября 2026:** обновлены [CHATGPT_GIT_BRIDGE.md](https://github.com/S960b/agent-dialogs/blob/main/CHATGPT_GIT_BRIDGE.md) и [AGENTS.md](https://github.com/S960b/agent-dialogs/blob/main/AGENTS.md); исторические `dialog_view`/`dialog_append` отделены от доступных `dialog_index`/`dialog_notice`; описан обязательный preflight.
- **8 ноября:** согласно [release report P36.2](https://github.com/S960b/agent-dialogs/blob/main/docs/P362_RELEASE_CLOSED_20261108.md), P36.2 был установлен и принят в сквозном тесте. Это историческое подтверждение, **не** непрерывный мониторинг работающих процессов.
- **10 ноября:** обмен сообщениями через Git-мост использовался в исследованиях NEWS/Hyperliquid/архивации. Наличие доступного Git-ответа не означает проверенный текущий PID всех локальных служб.

## Актуальная модель

| Уровень | Назначение |
|---|---|
| ChatGPT и MCP `dialog_*` | Создание и индексация задач, проверка состояния |
| GitHub `S960b/agent-dialogs` | Ветки `dialogs/<task_id>`, JSON сообщений и статуса |
| Authority | Валидация, права записи и порядок ходов; локальные сокеты для ChatGPT/MT |
| MT Git Inbox | Доставка поручения в нужную локальную MT-сессию; отдельные состояния `seen` и `inflight` |
| Taskboard | Веб-интерфейс списка задач и истории, локально 127.0.0.1:8787 |
| Git main | Код, документация, релизы, тесты; **не** активные переписки |

Для конкретной задачи: получить точный `task_id`, проверить `dialog_index`/`dialog_health`, затем прочитать тело по `HEAD` через GitHub. Не считать `observed:true` равным доставке в MT. Не дублировать сообщения при неопределённом результате. Не менять работающие службы, токены, БД и плагин без отдельного поручения.

## Локальный компонент старого моста и совместимость

Документация `S960b/agent-dialogs` отмечает, что установленная MCP-обвязка могла продолжать использовать отдельный файл `/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py`. Поэтому при переносе **сохранён** совместимый путь: `research/hermes_chat_bridge` теперь символическая ссылка на `../hermes_chat_bridge/legacy` (относительно каталога `research`). На Linux после Git checkout старый путь по-прежнему должен указывать на исходный `bridge_server.py`.

Это **только перенос структуры GitHub-репозитория**. Мы не перенастраивали работающий MT, systemd, Tailscale, Taskboard, authority, сокеты, токены или их конфигурацию. После фактического локального checkout нужно отдельно сверить ссылку, служебные пути и настройки, прежде чем убирать любые старые файлы.

## Что читать в новом чате

1. [S960b/agent-dialogs/AGENTS.md](https://github.com/S960b/agent-dialogs/blob/main/AGENTS.md).
2. [S960b/agent-dialogs/CHATGPT_GIT_BRIDGE.md](https://github.com/S960b/agent-dialogs/blob/main/CHATGPT_GIT_BRIDGE.md).
3. При вопросе о конкретной неисправности — текущий `task_id`, HEAD, `state.json`, соответствующий файл `messages/` и [документацию P32.5](https://github.com/S960b/agent-dialogs/blob/main/docs/P325/README.md).

**Не использовать** `legacy/readmefirst.txt`, `legacy/prompt_recover_for_AI.txt` и `legacy/CURRENT_STATE.md` как актуальную инструкцию по переустановке.

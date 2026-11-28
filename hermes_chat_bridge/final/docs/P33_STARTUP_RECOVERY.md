# P33: восстановление запуска Hermes Bridge

**Статус на 4 ноября 2026: УСТАНОВЛЕНО.** P33 интегрирован с P32.5 в коммите `76e5dbae4043327f04f9560566cff85d45e48ec9`, проверен полным тестовым набором и установлен в локальную службу. По последующей проверке P34: `hermes-bridge.service` активен, effective `Restart=on-failure`, `RestartSec=5s`, оба authority-сокета и порт 8765 работают. Проверенный исходный кандидат `151353404a1d8ad168d869fd960159d3a978f589` был объединён с P32.5, а **не установлен вместо него**. Ниже сохранены исторические описание проблемы, тестовый и установочный runbook. **Не повторять установку по этому документу на работающей системе.**

## Инцидент

В Git-диалоге task_58deb02895d43f1548bdf265fe118bd8 MT описал падение Hermes Bridge: оставшиеся authority-cg.sock и authority-mt.sock без живого процесса блокировали все последующие запуски. gateway_lifespan.py скрывал первичную причину под authority_host_failed, Restart=no отключал автоматический перезапуск. MT восстановил сервис вручную. P33 предназначен для предотвращения повтора.

## Ограниченное изменение

1. Новый src/git_dialog/socket_recovery.py. Файл authority-start.lock в приватном каталоге 0700 держит advisory flock на весь срок жизни UnixServer. Файл блокировки никогда не удаляется, а само блокирование освобождается ядром после гибели процесса.
2. Прежде чем убрать оставшийся сокет, проверяются оба endpoint: UID, тип Unix socket, режим 0600, nlink=1, size=0, предел длины пути и совпадение device/inode. Пробный AF_UNIX connect с timeout 200 мс: успешное соединение = живой endpoint и отказ; ECONNREFUSED = кандидат на очистку; остальные ошибки = отказ. После пробы и перед unlink проводится повторная проверка inode. Если что-то неизвестно, отказ без удаления.
3. src/git_dialog/unix_authority.py удерживает lock до закрытия собственных endpoint.
4. src/git_dialog/gateway_lifespan.py регистрирует в stderr/journald только фиксированный код ошибки, не тело исключения, секрет или traceback.
5. `deploy/93-p33-bridge-restart.conf`: проверенный drop-in; установлен в user systemd. `Restart=on-failure`, `RestartSec=5s`, `StartLimitBurst=3`, `StartLimitIntervalSec=300`.
6. tests/test_p33_socket_recovery.py: отдельные регрессионные тесты в tmp_path.

Граница безопасности: flock защищает согласованные экземпляры службы, а не враждебный процесс с тем же UID, способный менять приватный каталог. Такая компрометация находится вне угроз P33. Остаётся небольшой TOCTOU-интервал между проверкой inode и unlink, который в доверенном приватном каталоге покрывается межпроцессной блокировкой. Ни один подозрительный сокет автоматически не удаляется.

## Gate A. Только тесты, без изменений live

MT проверяет точный кандидат SHA в новой изолированной копии:

    git rev-parse HEAD
    PYTHONPATH=src /home/kali/.venvs/bridge-clean/bin/python -m pytest tests/test_p33_socket_recovery.py -q
    PYTHONPATH=src /home/kali/.venvs/bridge-clean/bin/python -m pytest tests/ -q
    PYTHONPATH=src /home/kali/.venvs/bridge-clean/bin/python -m compileall -q src tests
    git status --short

При необходимости использовать уже известный venv-PYTHONPATH: src:/home/kali/.venvs/bridge-clean/lib/python3.13/site-packages. Не трогать пакеты или Python живой службы. Обязательно проверить чистый запуск, stale после краша, живой listener, двойной запуск, невалидный inode/mode/symlink, неизвестный connect, сохранение истории и отсутствие Git-записей. Всі сценарии должны пользоваться временным каталогом и fake runtime.

MT НЕ меняет исходники и тесты. Если проверка красная, MT возвращает файл/строку, ожидаемое и фактическое, версии Python и точный SHA; ChatGPT сам исправляет код и публикует новый коммит.

## Gate B. Исторический сценарий релиза (уже выполнен, не повторять)

- Проверить production SHA, режимы доступа, статус 8765 и taskboard 8787, отсутствие активной операции. Сделать резервные копии исходника, установленных units/drop-ins и manifest, не копируя секреты в Git.
- Сопоставить конкретный опубликованный candidate SHA, выполнить Gate A и проверить systemd drop-in через systemd-analyze verify / systemctl --user cat.
- Во время согласованного окна: остановить только hermes-bridge.service; применить точный Git SHA, разместить 93-p33-bridge-restart.conf, daemon-reload, один start. Не трогать taskboard, MT plugin, SQLite, state, seen/inflight и Git conversation refs.
- Приёмка: сервис active, /ping=ok на 8765, оба Unix слушателя активны, dialog_list и dialog_view читаются, Restart=on-failure и StartLimit корректны. Убедиться, что Chat-45 P32.5 продолжает работать. Не посылать тестовый append без отдельного разрешения.
- Rollback: stop только bridge, вернуть старый код и unit drop-in из защищённой копии, daemon-reload, start. Не восстанавливать старые state SQLite и не удалять сокеты вслепую.

Автозапуск после перезагрузки зависит также от состояния user systemd: read-only проверить systemctl --user is-enabled hermes-bridge.service и loginctl show-user kali -p Linger. Никогда не включать linger автоматически: отдельное решение владельца.

## Критерий завершения

Все регрессионные и существующие тесты зелёные, установочная проверка читает актуальные диалоги, после аварии не остаётся постоянной блокировки запуска, а чужой живой endpoint не удаляется. При неизвестном состоянии STOP и одно точное сообщение владельцу. Никаких дополнительных функций в P33.

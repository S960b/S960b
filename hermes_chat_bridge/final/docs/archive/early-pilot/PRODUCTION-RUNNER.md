# P3 candidate: отдельный foreground runner, пока synthetic-only

P2c принят: MT469passed/compileall0/clean, syntheticHTTPselftest exit0/stderr0,
11 ожидаемых статусов/periodic1+wakeup1/known page SHA/shutdown; elapsed1.34s
по измеренному MT-отчёту. Локальные HEAD/soleparent/tree подтверждены отдельным
read-only stdout:3f7043a6b4dac4718701fe16015992cfb60cb3fd,
57b7af256e7d7b054784eb7c9fb8547f25c5a94e,1d9a0964aa00a04c172c025bc768af0e7a2db696.
41символ в предыдущем отчёте был опечаткой, не сменой кода. Повторов теста нет.

Новый runner.py — кандидат runtime, не установленный production-компонент.
Сейчас **только synthetic**, никакого GitAPI/PAT/bridgeforwarding. P1 на8789
сохраняется. Порты8787/8789 запрещены, default8790/127.0.0.1. Новое init создаёт
НОВУЮ отдельную state700/panel.sqlite600 и marker. Повторное init запрещено;
serve не использует немаркированную P1БД. flock не даёт два процесса с одной state.

Управляемый host связывает проверенные WSGI/gate/одинserialloop. Отдельный runner
thread вызывает только tick, workerHTTP только принимает pending. StrictChannel
из P2c локален для нового listener, не патчит Waitress глобально. finally закрывает
только собственные channels, останавливает syntheticloop/worker и проверяет
завершение. Никаких служб/systemd/cron/подписок/Funnel/старых конфигов.

Wakeup defaultoff. Только явный --enable-wakeup позволяет интерактивно указать
выделенный ТЕСТОВЫЙ ключ64lowercasehex; значение не генерируется/не пишется в
файл/env/argv/Git/БД/логи, не ищется в существующих секретах, не берётся из PAT/
пароляowner/подписки. Provisioning реальных ключей/forwarder этим НЕ выполнено.
Нельзя использовать публичный ключ tests как credential. Команды ниже описывают
интерфейс КАНДИДАТА для будущего локального испытания, не поручение MT выполнить:

```sh
PYTHONPATH=src python -m git_dialog.runner init --state-dir NEW_ABSENT_STATE
PYTHONPATH=src python -m git_dialog.runner serve --state-dir NEW_ABSENT_STATE
```

init спрашивает отдельный пароль owner скрыто. Нет CLI параметров GitHub, PAT,
адреса bind или передачи пароля/key через аргументы. Остановка Ctrl+C/SIGTERM
касается только собственного foreground-процесса. Старый P1не трогать.

ChatGPT:479passed/compileallOK,10новыхofflineпроверок. Провереныпорты,новаяstate/
защитаP1БД/defaultsynthetic/wakeupoff/lock/keyнеэхо/CLIиshutdownwiring наFakeServer.
Это НЕ фактический запуск нового serve_candidate с sockets, не browser-приёмка.
До реального запуска необходим отдельный ограниченный синтетический smoke
ИМЕННО нового runner. Ранее P2cсмок проверял host/strictparser, не этот CLI.

Единственное поручение MT текущего этапа: SSH-fetch exactSHA от ChatGPT в новую
чистуюкопию, отдельныйtestenv/systemPython/env-uAPPDIR-uAPPIMAGE приAppImage,
requirements-service.txt/pytest9.1.1. Только revparse/version, offlinepytesttests-q
(479), compileall/status. Никаких init/serve/smoke/HTTP/ключей/паролей/БД/старых
8789/8787/служб/OAuth/cron/подписок/refs/close/cancel/delete/create/recover.
MT не пишет/исправляет код, возвращает фактический отчёт и ждёт ревью.

Дальше: отдельная bounded приёмка runtime; затем reviewed real-reader opt-in,
safe key provisioning и разрешённая точка forwarder. Единый authority всех writers
не развёрнут; realdeletedisabled. Не объявлять весь production-проект готовым.

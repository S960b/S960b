# P2b: единый последовательный цикл чтения (изолированное wiring)

P2a принят MT: exacta48c41c80c0b0b675ff929ff00fefaa3d71d0e92,
433passed/Python3.13.3/compileall0/clean, без deployment. Настоящий документ —
следующий кодовый шаг, не команда изменить работающую панель или мост.

`wakeup_host.py` соединяет P1 PanelApplication и P2a WakeupApplication/Gate:

- WakeupHost(panel, key=None, interval=60) по умолчанию без wakeup (404).
  Ключ передаётся только trusted host-конфигурацией, не HTTP/JSON/owner-паролем.
  Конструктор не читает сеть/секреты/БД, не открывает listener, не запускает поток.
- HTTP-вызов только принимает проверенный сигнал; он не выполняет Git GET.
- `host.loop.tick()` — единственная точка чтения для будущего runner.
  Один mutex защищает весь refresh: два tick не читают параллельно.
  При совпадении periodic deadline и pending выполняется один refresh, не два.
  При раннем wakeup он выполняется сразу на следующем tick; periodic GET остаётся
  fallback. После медленного чтения deadline считается от завершения, без
  catch-up шторма. На HTTP202 нельзя полагаться как на уже выполненное чтение.
- `refreshed=true` означает выполненную ПОПЫТКУ чтения. Для успеха дополнительно
  проверять reader.status().error; новый цикл не доказывает работу writer/worker.
  Ошибка источника даёт прежний fail-closed503, немедленного retry нет.
- `loop.stop()` не допускает новых чтений после возврата, но ждёт уже идущий GET.
  Это не принудительная отмена socket/потоков. Таймауты задаёт transport.
  Runner отдельно прекращает listener/входящие запросы: остановка reader-loop
  сама не закрывает HTTP и не является доказательством сетевого shutdown.
- Все periodic и wakeup чтения должны идти через ЭТОТ loop; прямые внешние
  вызовы reader.refresh()/gate.drain() обходят координацию и не разрешены runner.
  Тела событий не выбирают callbacks/файлы/shell/модель/actor/права.

## Проверки

ChatGPT Python3.12.14/pytest9.1.1: **453passed**, compileallOK, 20 новых тестов
поверх433. Полная offline цепочка WSGI→проверенная подпись→pending→serialtick→
SnapshotReader→FakeApiGET-only; без сети/subprocess, SQLite толькоtmp_path.
Проверены defaultdeny/разделениеowner/HMAC, coalescing, ранний wakeup/fallback,
конкурентныеticks+stop без параллельныхrefresh, медленныйGET/clock/interval bounds,
ошибкиисточника503безнемедленногоповтора. Это не фактическая HTTP/forwarder-проверка.

MT получает ровно одно поручение: fetch сообщённого ChatGPT exactSHA в НОВУЮ
чистую кодовую копию, отдельное testenv (requirements-service.txt/pytest9.1.1,
системныйPython/env-uAPPDIR-uAPPIMAGE приAppImage). Никаких reset/force/правок.

```sh
git rev-parse HEAD
python --version
python -m pytest --version
PYTHONPATH=src python -m pytest tests -q
python -m compileall -q src
git status --short
```

Ожидается453passed, compileall0, clean. Сообщить SHA/Python/pytest/summary/exit.
НЕ выполнять init/serve/smoke/HTTP/GitAPI/worker, не открывать PAT/пароль/key/их пути,
не создавать credentials/БД/подписки и не оборачивать живой P1 этим host-классом.
Установленные8789/8787, мост/службы/OAuth/cron/истории/старыеcanaryБД/refs не менять.
MT код не пишет/не исправляет. После отчёта — ревью ChatGPT, без ACK-циклов.

Остаётся следующий отдельный шаг: bounded synthetic HTTP-проверка готового
host/runner на loopback port0 с временной БД и ephemeral test-key, без реальных
данных и изменения служб. После этого — отдельный reviewed opt-in provisioning
и разрешённая точка event-forwarder. CLI P1/service.py этим шагом не изменены:
само появление класса в Git не подключает event-поток и не заменяет общий gateway.

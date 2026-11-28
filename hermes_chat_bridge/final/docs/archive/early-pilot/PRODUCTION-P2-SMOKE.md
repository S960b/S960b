# P2c: bounded synthetic HTTP/self-test готового wakeup host

P2b57b7af256e7d7b054784eb7c9fb8547f25c5a94e принят по фактическому MT-отчёту:
Python3.13.3/pytest9.1.1/453passed/compileall0/clean/20hostтестов. ChatGPT независимо
сверил code/main/dialogrefs. Это не установило wakeup/forwarder в живой P1.

## Что добавлено

`python -m git_dialog.wakeup_smoke --self-test` — единственный CLI этого модуля.
Нет параметров config/path/data/key/port/host. Он создаёт новую временную БД
(отдельный state700/panel.sqlite600), случайный owner-пароль и отдельный
ephemeral32byteHMACkey только в памяти, синтетический SnapshotReader БЕЗtransport.
Маркерный event содержит нашу task_id только как фиксированный routing-ID;
реальных сообщений/задач/PAT/GitAPI/старыхБД/подписок не читает.

Listener жёстко127.0.0.1:port0; запрещены привилегированный порт/8787/8789.
Не Funnel/LAN/существующая панель/служба. Waitress3.0.2, connectionlimit8,
header8192/body4096/timeouts1s/Connectionclose. На WSGI максимум16 запросов;
независимый watchdog10s закрывает ТОЛЬКО собственную карту sockets и stop-runner.
finally останавливает serial loop, Waitress workers, joins threads/cancels timer;
наружный timeout25s обязателен при испытании MT. Это не доказательство shutdown
реального Git-reader с сетью: в этом тесте reader чисто синтетический/no-IO.

Локальный StrictParser/StrictChannel применяются ТОЛЬКО к этому новому listener,
не глобально и не к живому P1. До WSGI отклоняются дубликаты headers, TE/Expect,
underscored/proxy headers, absolute URI/fragment; после parsing — Connectionclose.
Это важно: стандартный Waitress нормализует некоторые заголовки до WSGI, одной
проверки env недостаточно. Private subclass API проверен только с pin3.0.2.

Один runner выполняет host.loop.tick; HTTP только ставит pending. Результат
должен показать ровно одну periodic и одну wakeup ПОПЫТКУ синтетического чтения.
Здесь нет исполнения/claim/ACK/получения job/remote handoff/Git-записи.

## Фактическая проверка ChatGPT

Python3.12.14/pytest9.1.1: **469passed**, compileallOK (453+16новых).
Offline16 проверяют parser/framing/CLI/no-argument-config/редакциюошибок/ownership
закрываемых channels. Полный набор по-прежнему блокирует сеть/subprocess и
разрешает SQLite толькоtmp_path; реальный smoke запускается отдельно от pytest.

Финальный код: один bounded запуск exit0, stderr0bytes, safeJSON:

```json
{
  "status": "passed",
  "http_statuses": [401,200,202,200,401,200,400,405,405,400,400],
  "reader_attempts": {"periodic":1,"wakeup":1},
  "source": "synthetic",
  "temporary_database": true,
  "ephemeral_test_key": true,
  "real_data_git_credentials_used": false,
  "old_services_changed": false,
  "page_sha256": "db6e8394c011a9a88dd808030b1f18d136a98fe47efd308d372846903e0595e0",
  "shutdown": "threads_joined_sockets_closed"
}
```

Первые9 проверок: unauthGET401/ownerGET200/signedPOST202/duplicate200/
unsigned401/foreign-task200ignored/invalidschema400/GETwakeup405/DELETE405.
На них проверяются no-store/nosniff/size; ownerHTML содержит только fixture.
Последние2 — raw неоднозначный Content-Length и TE+CL, parser400 ДО WSGI;
security headers приложения на parser-generated400 не заявляются.
Численная длительность отдельно не измерялась; timeout25s не сработал.

## Одно поручение MT (не установка)

Fetch exactSHA, сообщённого ChatGPT, в НОВУЮ чистую кодкопию без reset/force/правок.
Отдельное testenv, requirements-service.txt/pytest9.1.1, системный Python и
env-uAPPDIR-uAPPIMAGE при необходимости. НЕ venv P1/служб.

```sh
git rev-parse HEAD
python --version
python -m pytest --version
PYTHONPATH=src python -m pytest tests -q
python -m compileall -q src
git status --short
```

Если offline зелёный469 и compileall0, ровно ОДИН запуск:

```sh
timeout 25s env PYTHONPATH=src python -m git_dialog.wakeup_smoke --self-test
```

При ошибке/timeout НЕ повторять и не обходить ограничения, вернуть exit/safeerror.
При успехе — SHA/Python/pytest/summary/compileall/status и фактический safeJSON,
exit/stderr/остановка (elapsed только если измерен, не придумывать).
Не выводить пароль/key/заголовкиAuthorization/подписей/телаHTML/credentialPATH.
Тест сам убирает только созданную им новую временную БД/каталог.

Никаких дополнительных init/serve/browser/Funnel/realdata/PAT/GitAPI/worker/
новыхподписок/живыхcredentials/прав/сроков/ОAuth/cron/служб/refs/close/cancel/
delete/create/recover. Не трогать установленные8789/8787, мост и старые истории/
registry/journal/worker-canaryБД. MT не пишет/не исправляет код. Ожидать ревью.

После приёмки станет проверена только synthetic HTTP-цепочка. По-прежнему нужны
отдельные reviewed production-runner/локальное provisioning выделенного ключа
и разрешённая opt-in точка event-forwarder. Этот пакет не меняет P1 CLI/service.py,
не подключает действующий мост/подписки и не создаёт общую authority всех writers.

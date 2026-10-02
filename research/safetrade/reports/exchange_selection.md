# Выбор трёх внешних источников цен (Этап 1)

Дата получения рейтинга: 2026-10-02T12:40Z (UTC)
Источник рейтинга: CoinGecko /api/v3/exchanges (750 площадок, 3 страницы по 250)
Сырой ответ: data/raw/coingecko_exchanges/raw_exchanges_2026-10-02.json
Единица объёма: trade_volume_24h_btc (BTC)
Метод сортировки: числовой spot volume 24h по полю trade_volume_24h_btc, убывание
Полнота выборки: полный листинг CoinGecko (750 площадок); DEX/деривативы исключены вручную по id/имени (в топ-5 не попали)

ВАЖНО: рейтинг по trade_volume_24h_btc — заявленный агрегатором объём, НЕ доказательство
реального качества торговли. Trust Score не использовался для сортировки (по ТЗ),
но используется как сигнал при оценке пригодности площадки.

## Топ-5 по заявленному spot volume (на 2026-10-02 12:40Z)

| # | Exchange | Volume 24h (BTC) | Trust | REST | WS | Вердикт |
|---|----------|-----------------|-------|------|----|---------|
| 1 | Binance  | 153960 | 10 | 200 OK | OK | ПРИГОДЕН |
| 2 | CoinUp.io| 87610 | 3 | 403 Cloudflare challenge | – | НЕПРИГОДЕН |
| 3 | BTCC     | 86994 | 3 | timeout | – | НЕПРИГОДЕН |
| 4 | Pionex   | 57554 | 7 | 200 OK | WS 404/нет потока | НЕПРИГОДЕН (нет WS) |
| 5 | Tapbit   | 39918 | 6 | DNS не резолвится | – | НЕПРИГОДЕН |

Пригодных из топ-5 — только одна (Binance). По ТЗ п.2: «Если пригодных меньше трёх,
показать причину; замены из следующих мест оформить явно, не выдавать за исходный топ-5».

## Причины негодности топ-5 (кроме Binance)

- **CoinUp.io** (2-е место): все REST-пути отдают Cloudflare «Just a moment...» JS-челлендж (403).
  Публичный программный доступ без браузерного прохождения челленджа невозможен; Trust Score 3.
- **BTCC** (3-е место): соединение с api.btcc.com таймаутит (12+ сек), ответа нет. Публичной API-документации не обнаружено.
- **Pionex** (4-е место): REST /api/v1/market/tickers работает (200). WS: первичная проверка
  api.pionex.com/ws -> 404. ПОВТОРНАЯ проверка по ревью: официальный публичный endpoint
  wss://ws.pionex.com/wsPub отвечает (не 404), op=SUBSCRIBE принимается сервером
  (ошибка INVALID_SYMBOL на формате символа), т.е. endpoint доступен. Формат символа/канала
  по документации не удалось воспроизвести за разумное время — помечаем «не подтверждено»,
  а не «нет WS». Pionex не входит в выбранную тройку: для исследования задержек нужен
  событийный поток, а он не подтверждён.
- **Tapbit** (5-е место): api.tapbit.com / api.tapbit.io не резолвятся (DNS NXDOMAIN).

## Замены из следующих мест (явно, НЕ топ-5)

Проверенные запасные в порядке убывания заявленного объёма (6+, но не ограничиваясь):

| Место | Exchange | REST | WS | Общих USDT-пар с SafeTrade |
|-------|----------|------|----|---------------------------|
| 6  | OKX      | 200 OK | OK (books5, tickers) | 11 |
| 7  | WEEX     | DNS fail (api.weex.com) | 521 Cloudflare origin error | – |
| 8  | LBank    | api.lbank.info отдаёт HTML-ошибку | – | – |
| 9  | KCEX     | HTML ERROR | – | – |
| 10 | Coinbase | 200 OK (BTC-USD) | не проверялся | 4 (котировка USD, не USDT — не подходит по ТЗ) |
| 13 | Gate     | 200 OK | OK (spot.book_ticker) | 17 |
| 15 | Bybit    | 200 OK | OK (orderbook, publicTrade) | 11 |
| –  | MEXC     | 200 OK | WS «Blocked!» (регион) | 23 |

Примечания:
- WEEX: REST-хост api.weex.com не резолвится из фактической среды; WS ws-spot.weex.com
  отвечает 521 (Cloudflare origin error). Документация обещает V3, но доступ из этого региона/сети не работает.
- MEXC: REST работает, но WS-поток wbs.mexc.com отвечает «Not Subscribed... Reason: Blocked!» — региональная блокировка.
- LBank: api.lbank.info отдаёт HTML-страницу «请求的服务找不到», не JSON.

## Выбранная тройка

**Binance, OKX, Bybit** — выбраны по критериям ТЗ п.2: общая пара с SafeTrade,
качество стакана (полные snapshot+delta потоки), непрерывность (20-час. аптайм-тест в Этапе 3),
документированный публичный API без ключа.

- Binance: REST api.binance.com + WS stream.binance.com:9443, bookTicker + depth20@100ms, serverTime.
- OKX: REST www.okx.com/api/v5 + WS ws.okx.com:8443/ws/v5/public, books5 + tickers.
- Bybit: REST api.bybit.com/v5 + WS stream.bybit.com/v5/public/spot, orderbook + publicTrade.

Независимость ликвидности: Binance, OKX и Bybit — три разных маркет-мейкера и три
независимых стакана. Это не агрегаторы одной ликвидности (в отличие, например,
от Pionex, который агрегирует Binance/Bybit/OKX через своих ботов — ещё одна причина отказа).

Общие пары с SafeTrade (по markets API SafeTrade, 273 рынка, 56 USDT-пар):
BTC/USDT, ETH/USDT, DOGE/USDT, LTC/USDT присутствуют на всех трёх биржах.
Диагностический рынок: BTC/USDT. Дополнительные: ETH/USDT, DOGE/USDT (по факту данных).

## SafeTrade (исследуемая площадка) — проверка публичного API

- Основной сайт safetrade.com; example-client указывает baseURL https://safe.trade/api/v2.
- REST публичный (без ключа) РАБОТАЕТ: https://safetrade.com/api/v2/trade/public/markets (200, 87КБ),
  .../trade/public/markets/btcusdt/depth?limit=10 (200, asks/bids [[price,qty]]),
  .../trade/public/markets/btcusdt/trades?limit=10 (200), .../trade/public/tickers (200).
  Доступ с UA Firefox; без UA ранее наблюдался 403 Cloudflare (подтверждает заметку в ТЗ).
- WS публичный: wss://safe.trade/api/v2/websocket/public — работает, подписка
  {"event":"subscribe","streams":["global.tickers","btcusdt.depth","btcusdt.trades"]}.
  Формат depth: {"btcusdt.depth":{"asks":[[price,qty]],"bids":[...],"sequence":N}} — delta-обновления
  с sequence. Подтверждено: global.tickers и btcusdt.depth приходят в течение 25 сек.
- ВАЖНО для адаптера: первый WS-коннект к safe.trade периодически таймаутит (Cloudflare),
  ретрай с Origin: https://safetrade.com проходит. Учесть в reconnect-логике.
- Торговый REST требует ключ (401 authz.invalid_session без него) — по ТЗ ключи не запрашиваем,
  торговать не будем.

## Коллизия QTC (из ТЗ п.3)

На SafeTrade в списке markets есть QTC/USDT (Quantus). На Gate/MEXC QTC — тоже есть
(проверить market id при использовании). В выбранной тройке (Binance/OKX/Bybit) QTC
отсутствует, поэтому коллизия QTC не влияет на текущий набор; при расширении рынков
сверять market id, не тикер.

## Версии API и endpoints (проверено живьём 2026-10-02)

- Binance: REST /api/v3 (serverTime, bookTicker, depth), WS wss://stream.binance.com:9443/ws — подтверждено.
- OKX: REST /api/v5/market/ticker?instId=BTC-USDT, WS wss://ws.okx.com:8443/ws/v5/public — подтверждено. (В changelog OKX упоминался переход WS-порта; порт 8443 работает.)
- Bybit: REST /v5/market/tickers?category=spot, WS wss://stream.bybit.com/v5/public/spot — подтверждено.
- SafeTrade: REST /api/v2/trade/public/*, WS wss://safe.trade/api/v2/websocket/public — подтверждено.

## Оговорки

- Объёмы из CoinGecko — заявленные, не доказательство качества.
- «Топ-5» здесь = топ-5 из доступной выборки CoinGecko на момент 2026-10-02T12:40Z; WEEX/LBank/MEXC
  могут быть доступны из других регионов — перепроверить при смене сети.
- Набор источников зафиксирован для отдельного эксперимента версии 1 (binance, okx, bybit + safetrade).
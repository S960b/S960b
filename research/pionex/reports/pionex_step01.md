# Pionex — шаги 0+1 выполнены (публичные данные, без ключа)

## Шаг 0: таблица эндпоинтов (все публичные, без авторизации)

| Endpoint | Параметры | Лимит | Поведение (проверено) |
|---|---|---|---|
| GET /api/v1/market/tickers | type=SPOT\|PERP, symbol? | weight 1 | SPOT=328, PERP=607 |
| GET /api/v1/market/bookTickers | type=SPOT\|PERP, symbol? | weight 1 | 328/606, best bid/ask |
| GET /api/v1/market/depth | symbol, limit 1-1000 | weight 1 | snapshot bids/asks, updateTime |
| GET /api/v1/market/trades | symbol, limit 10-500 | weight 1 | поля tradeId, price, size, side(BUY/SELL, taker perspective), timestamp |
| GET /api/v1/market/klines | symbol, interval, limit 1-500, endTime | weight 1 | max 10000 записей; история вглубь до 2021 через endTime |
| GET /api/v1/market/fundingRates | symbol, limit 1-500, endTime | weight 1 | интервал ровно 8ч; 500 записей ≈ 167 дней |
| GET /api/v1/market/markKlines | symbol, interval, limit | weight 1 | mark price klines |
| GET /api/v1/market/indexKlines | symbol, interval, limit | weight 1 | index price klines |
| GET /api/v1/market/openInterests | (symbol игнорируется) | weight 1 | возвращает ВСЕ символы списком; фильтровать локально |
| Earn/Dual, Arb | (см. earn-api) | — | публичные products/quotes — не углублялся |

Rate limits: 10 req/s по IP (общий), weight 1 на endpoint, 429 + 60с бан.
Формат символов: SPOT = BASE_QUOTE (AAA_USDT), PERP = BASE_QUOTE_PERP.

## Факты по глубине данных (эмпирически проверено)
- klines 1D: limit 500 за запрос; через endTime доступна история минимум
  до 2021 (BTC_USDT: 500 свечей от 2021-04-16, 500 от 2024-04-20 и т.д.).
- fundingRates: 500 записей, интервал ровно 8.00ч, диапазон 2026-04-22..2026-10-06
  (≈167 дней, 5.5 мес).
- openInterests: параметр symbol НЕ фильтрует — возвращает все перпы.
- Ошибок при сборе: 0. Совпадение spot/perp баз: 196 рынков.

## Шаг 1: shortlist funding carry (25 рынков, полная funding-история 500×8ч)

Funding в bps за период 8ч; bps/day = mean×3; breakeven_days(fee) = дни,
за которые funding покрывает 4 ноги круга (spot entry, perp entry, spot exit,
perp exit) при указанной комиссии НА НОГУ.

| symbol | fund bps/8h | pos% | bps/day | spot_sp | perp_sp | BE@1bps | BE@5bps | BE@10bps |
|---|---|---|---|---|---|---|---|---|
| AMZNX_USDT | 0.800 | 82.6 | 2.399 | 0.79 | 0.40 | 1.67 | 8.34 | 16.67 |
| ARKM_USDT | 0.603 | 86.2 | 1.808 | 7.58 | 7.58 | 2.21 | 11.06 | 22.12 |
| AR_USDT | 0.601 | 85.6 | 1.803 | 22.32 | 2.23 | 2.22 | 11.09 | 22.18 |
| ATH_USDT | 0.480 | 99.0 | 1.440 | 6.87 | 13.76 | 2.78 | 13.89 | 27.78 |
| AEVO_USDT | 0.448 | 97.0 | 1.345 | 38.61 | 3.86 | 2.97 | 14.87 | 29.73 |
| ASTER_USDT | 0.446 | 96.4 | 1.339 | 27.06 | 1.35 | 2.99 | 14.93 | 29.87 |
| 1INCH_USDT | 0.421 | 75.6 | 1.263 | 18.96 | 9.48 | 3.17 | 15.83 | 31.67 |
| ALT_USDT | 0.403 | 93.2 | 1.210 | 12.82 | 12.82 | 3.31 | 16.53 | 33.06 |
| AAVE_USDT | 0.355 | 78.0 | 1.065 | 0.55 | 0.55 | 3.76 | 18.78 | 37.56 |
| AAPLX_USDT | 0.335 | 73.4 | 1.004 | 0.30 | 0.30 | 3.98 | 19.92 | 39.84 |

Годовая проекция funding (при стабильном среднем): 2.40 bps/day ≈ 8.8%/год
(AMZNX) до 16.5%/год (топ-8 bps/day… фактически 0.8bps/8h ≈ 8.8%/год);
у AAPLX 1.0 bps/day ≈ 3.7%/год. Это carry-доход ГРУБО, до вычета комиссий,
basis-риска и стоимости капитала.

## Ограничения (честно)
- Комиссии Pionex (spot maker/taker, futures maker/taker) НЕ подтверждены —
  фактические ставки владельца неизвестны. Поэтому BE считается для
  1/5/10 bps на ногу как стресс.
- funding carry требует ДВУХ ног одновременно (spot + perp) и удержания;
  доход = funding − комиссии (4 ноги) − basis-риск − стоимость капитала.
  funding не гарантирован: pos% 73-99%, но встречаются отрицательные периоды.
- Спреды spot/perp — из bookTickers на момент замера (1 снимок), не средние.
- При комиссии 5 bps на ногу (20 bps круг) лучший кандидат окупается за
  8.3 дня, далее чистый carry — но это при условии сохранения funding и
  отсутствия движения basis против позиции.

## Вопрос к тебе (шаг 2)
Достаточно ли данных для шага 2 (offline economics: funding net carry с
basis и fee stress, Dual payoff-grid против hold/cash, grid replay)?
Какие пары из shortlist брать в приоритет, какие отсечь по спреду/ликвидности
(например, AR_USDT spot_spread 22 bps — вход/выход дорогой)?
Нужно ли собирать публичный WS (шаг 3) на 2-3 shortlist-рынках — дай финальный
shortlist и критерии отбора.
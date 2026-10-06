# Pionex — шаг 2 (offline economics: funding carry), отчёт

Модель исправлена по замечаниям ревизии. Только публичные данные, без ключа.
Read-only ключ владельца проверен отдельно (см. ниже) — для анализа не требовался.

## 1. Проверка read-only ключа владельца (/home/kali/Documents/key/)
- GET /api/v1/account/balances -> 200, result=true, balances=[] (пусто, как и сказал владелец).
- GET /api/v1/trade/fills?symbol=BTC_USDT -> 200, result=true, fills=[].
- POST /api/v1/trade/massOrder (пустой) -> result=false, code=AUTH_UNAVAILABLE
  «have no right» => торговые права технически отсутствуют. Read-only подтверждён.
- Формат подписи (эмпирически уточнён, в доке неточно):
  HMAC-SHA256(hex) от `METHOD + PATH + '?' + QUERY(sorted by key) [+ body(compact JSON)]`,
  headers PIONEX-KEY / PIONEX-SIGNATURE, timestamp(ms) в query (±20с).
  Ключевые грабли: (а) query в каноне сортируется алфавитно; (б) body в каноне —
  компактный JSON без пробелов (иначе INVALID_SIGNATURE).

## 2. Исправленная модель carry (по замечаниям)
- all_in_cost = 4*fee_per_leg + spot_roundtrip_spread + perp_roundtrip_spread
  (+ basis_change учтён отдельно; slippage НЕ моделирован — ограничение).
- Знаменатель определён явно: доходность дана и на perp notional, и на
  gross capital (spot notional + perp notional = 2x notional).
- Посчитана реальная последовательность 500 выплат (8ч), rolling 7/30/90d,
  худшая отрицательная серия, доля дней с положительным PnL при выходе.
- basis: spot close vs perp mark на 8H-свечах в моменты funding (синхронно по time).
- Знак funding: принят стандарт (positive funding => longs pay shorts; позиция
  long spot + short perp ПОЛУЧАЕТ funding при >0). В документации Pionex явной
  формулировки знака не найдено — помечено как ДОПУЩЕНИЕ, требует подтверждения.

## 3. Результаты (500 периодов ≈ 167 дней; net = funding_total + basis_change − all_in)

fee = 5 bps на ногу (all_in ≈ 4*5 + спреды):

| symbol | fund_total bps | basis_chg | all_in | net bps | net/gross | worst_streak | exit_pos% |
|---|---|---|---|---|---|---|---|
| AMZNX_USDT | 399.9 | +1.6 | 22.0 | 379.5 | 189.7 | -1.1 | 98.6 |
| ARKM_USDT | 301.3 | +34.2 | 35.2 | 300.3 | 150.2 | -15.2 | 82.2 |
| AEVO_USDT | 224.2 | +57.3 | 66.3 | 215.2 | 107.6 | -7.4 | 63.2 |
| ATH_USDT | 240.0 | +9.4 | 40.6 | 208.8 | 104.4 | -4.0 | 83.6 |
| AR_USDT | 300.5 | -66.7 | 44.5 | 189.3 | 94.6 | -5.9 | 74.2 |
| ALT_USDT | 201.7 | +31.4 | 45.7 | 187.4 | 93.7 | -5.9 | — |
| 1INCH_USDT | 210.5 | 0.0 | 39.0 | 171.6 | 85.8 | -15.4 | — |
| ASTER_USDT | 223.2 | -19.6 | 34.8 | 168.8 | 84.4 | -1.1 | — |
| AAVE_USDT | 177.5 | +0.9 | 21.7 | 156.8 | 78.4 | -13.4 | 63.6 |
| AAPLX_USDT | 167.3 | -1.0 | 20.9 | 145.4 | 72.7 | -10.1 | — |

Диапазон по fee (на gross capital, за 167 дней → годовых):
- AMZNX: fee1 → 197.5 bps (≈4.3%/год), fee5 → 189.7 (≈4.1%), fee10 → 179.9 (≈3.9%).
- AAVE: fee1 → ~80 bps (≈1.7%/год), fee5 → 78.4, fee10 → ниже.
- AAPLX: fee5 → 72.7 (≈1.6%/год).
rolling 30d: AMZNX min/median/pos = +1.0/+49.4/100% (все 30-дн. окна положительны).
rolling 167d: n/a (нужно 501 период, есть ровно 500).

## 4. Важные ограничения
- AMZNX/AAPLX — токенизированные акции (xStocks/Backed, 24/7), базовый рынок США
  работает ~9:30–16:00 ET. Off-hours/weekend: цена от оракула, спред может
  расширяться, возможны corporate actions (дивиденды/сплиты) — НЕ подтверждено,
  нужен WS-наблюдатель в часы США и вне их.
- Спреды — 1 snapshot bookTickers, не p50/p90. Slippage не моделирован.
- basis_change из 8H-свечей (сопоставление по времени может быть неточным).
- Знак funding — допущение из общей семантики, не подтверждён докой Pionex.
- Доход 3–4%/год на gross capital при допущениях (funding сохраняется, basis
  стабилен). Это НЕ гарантия; funding может уйти в минус и держаться.

## 5. Запрос: шаг 3 (24ч public WS/REST forward-пилот)
Кандидаты: AAVE_USDT (чистый crypto, узкий спред 0.55/0.55 bps),
AMZNX_USDT (лучший funding, но xStock), AAPLX_USDT (xStock). Возможно ARKM_USDT.
Метрики, которые соберу: p50/p90/p99 спред ОБЕИХ ног; доступный объём
top-1/top-5 для $100/$500/$1000; staleness; reconnect; duplicates/gaps;
basis и mark-index deviation; доля времени, когда all-in break-even > 0;
для xStocks — отдельно часы США и закрытый период.
Подтверди shortlist и критерий готовности funding-шага. Ордера/ключ не нужны
(блокировка write подтверждена).
# SafeTrade приватный API — проверка read-only ключа

Дата: 2026-10-04. Хост: рабочий (8 ядер, 28 ГБ). Ключ: `~/Documents/safetrade/apikey`
(права 600, в репозиторий НЕ включён, в выводе не появляется).
Сбор данных maker-этапа продолжает работать (run mk_18db19212772b000, PRL/QUANTUS/LTC).

## 1. Документация авторизации

Биржа использует стек OpenWare (Peatio). Официальная док. того же стека:
https://www.openware.com/sdk/2.6/docs/peatio/api/trading-api

Схема подписи (проверена живьём, HTTP 200):
- `X-Auth-Apikey` — публичный ключ (16 hex);
- `X-Auth-Nonce` — миллисекундный Unix-время UTC (одноразовое число);
- `X-Auth-Signature` — `HMAC-SHA256(secret, nonce + apikey)` hex.

Это подтверждено: без заголовков → `401 authz.invalid_session`, с фейковым nonce →
`401 authz.nonce_expired`, с корректной подписью → 200.

Клиент: `scripts/private_api_probe.py` (+ `scripts/private_scan.py` — классификация).
Таймаут 20с, до 3 попыток, 429 → backoff 2/4с, ошибки сети отделены от API-ошибок.
Только методы чтения; никаких create/cancel/withdraw.

## 2. Проверка с текущей машины

| Операция | Результат | Ограничения | Источник |
|---|---|---|---|
| Авторизация (подпись) | OK (200) | — | probes выше |
| Балансы `/trade/balances` | 401 authz.invalid_permission | эндпоинт есть, права ключа не покрывают | private_scan |
| Балансы `/trade/account/balances` | 404 | неверный путь (в этой сборке нет) | private_scan |
| Открытые ордера `/trade/market/orders` | 200 | ордеров в состоянии wait нет (все done — история) | private_scan |
| История своих ордеров `/trade/history/orders` | 401 authz.invalid_permission | права ключа не покрывают | private_scan |
| История исполнений `/trade/history/trades` | 401 authz.invalid_permission | права ключа не покрывают | private_scan |
| Свои исполнения `/trade/market/trades` | 200 | PRL 11, QUANTUS 6, LTC 4 записи | private_scan |

Важно: пустой успешный ответ от ошибки доступа отличаем по HTTP-коду —
`200 []` (пусто) ≠ `401 {"errors":["authz.invalid_permission"]}`. Ключ read-only:
balances/history недоступны, orders/trades (свои) доступны.

## 3. Комиссии

| Источник | Значение |
|---|---|
| `GET /api/v2/trade/public/trading_fees` | maker=0.001, taker=0.001 (для всех рынков, market_id=any) |
| Свои сделки PRLUSDT (11 шт) | fee/notional = 10.0 bps (0.1%) — все |
| Свои сделки QUANTUSUSDT (6 шт) | fee/notional = 10.0 bps (0.1%) — все |
| Свои сделки LTCUSDT (4 шт) | fee в LTC (fee_currency=ltc); пересчёт в USDT = 10.0 bps |

Вывод: **maker = taker = 0.1% (10 bps) для всех трёх пар, включая аккаунт**.
Отдельного API-метода «комиссия моего аккаунта» нет; подтверждение двойное
(публичный тариф + фактические исполнения). LTC берёт комиссию в базовой
валюте (ltc), PRL/QUANTUS — в quote (usdt) — на ставку не влияет.

Исторические комиссии своих сделок (пример, суммы в валюте комиссии):
- PRL 2026-09-23 sell 8.3943 @1.46, fee 0.012255678 usdt (10 bps)
- QUANTUS 2026-10-02 sell 0.192 @136.001, fee 0.026112192 usdt (10 bps)
- LTC 2026-09-11 buy 0.75252 @53.15443, fee 0.00075252 ltc (= 10 bps от notional)

## 4. Правила рынков и ордерные методы

| Пара | Шаг цены | Шаг кол-ва | Мин. кол-во | Мин. стоимость* | Пост-онли |
|---|---:|---:|---:|---:|---|
| PRLUSDT | 0.01 (price_prec=2) | 0.0001 (amount_prec=4) | 2 PRL | ≈2.1 USDT | не документирован |
| QUANTUSUSDT | 0.001 (price_prec=3) | 0.001 (amount_prec=3) | 0.01 QUNT | ≈0.15 USDT | не документирован |
| LTCUSDT | 0.00001 (price_prec=5) | 0.00001 (amount_prec=5) | 0.0001 LTC | ≈0.0053 USDT | не документирован |

\* по последней цене. Источник: `GET /api/v2/trade/public/markets` (поля
price_precision/amount_precision/min_price/min_amount).

**Post-only:** в документации OpenWare/Peatio SDK 2.6 параметр post-only/ордера
`POST /trade/market/orders`... `DELETE /trade/market/orders/:id` присутствуют в
общей спецификации API-ключей, но отдельного флага post_only в документации НЕ
найдено (swagger биржи недоступен: /swagger, /api/v2/peatio/swagger,
late 404/000). Для будущего ордерного этапа сначала подтвердить наличие
post-only на бирже (пробный ордер вне книги и проверка ствола, либо ответ
поддержки биржи), не предполагая его наличие.

Методы будущего создания/чтения/отмены (по документации Peatio/OpenWare):
- POST /api/v2/trade/market/orders — создать ордер (limit/market);
- GET  /api/v2/trade/market/orders — свои ордера (проверено, работает);
- DELETE /api/v2/trade/market/orders/:id — отмена ордера.
Ничего из этого в рамках read-only проверки НЕ вызывалось.

## 5. Воспроизводимая команда

```bash
cd ~/safetrade-research
./venv/bin/python scripts/private_scan.py      # классификация всех методов
./venv/bin/python scripts/private_api_probe.py # точечная проверка: auth, orders, trades
```

В выводе нет секретов/балансов/личной истории: ключ читается из файла и не
печатается; балансы не доступны ключу; история отдаётся только в сводных
метриках (числа, ставки, даты) — отдельные ордера/суммы аккаунта в отчёт
не включены.

## Следствия для maker-кандидата

- Комиссия round-trip = 20 bps — подтверждена на аккаунте (не допущение).
- PRL: спред ≈ 1 шаг (0.01) — улучшать цену некуда, только очередь; при этом
  мин. заявка 2 PRL ≈ 2.1 USDT — бюджет 5/10 USDT допустим.
- QUANTUS: спред широкий, шаг 0.001, мин. заявка мала — улучшение котировки
  реально (потенциально лучше для maker-политики).
- Открытых ордеров на аккаунте нет; история тонкая (PRL 11 сделок с 07.2026) —
  данные об историческом поведении аккаунта ограничены, на решение не влияют.
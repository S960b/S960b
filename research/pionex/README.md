# Pionex research — funding carry (закрыто 2026-10-06)

Исследование биржи Pionex (https://www.pionex.com/docs/readme.md) как
кандидата для извлечения дохода, акцент на AI Kit / агентах. Ветка funding
carry (spot + short perp) исследована до конца и ЗАКРЫТА как непригодная
для реальной торговли на текущих доказательствах.

## Почему закрыто (вердикт внешнего ревьюера 2026-10-06)

- AAVE: расчётно +4.02 USDT за 166 дней при ретроспективно подобранном
  капитале 427.22 USDT (~0.94% за 5.5 мес).
- ARKM: +4.20 USDT за 166 дней при капитале 284.67 USDT (~1.48%).
- Одно из пяти независимых 30-дневных окон у каждой пары — отрицательное.
- Вся видимая прибыль (4.02–4.20 USDT) одновременно является максимальным
  запасом на НЕ учтённые расходы (спред, слайпедж, тариф, внутрипериодные
  скачки mark, синхронизация). Реальный бюджет ошибки съедает её целиком.
- Выживание без ликвидации НЕ подтверждено: залог подобран по известной
  будущей траектории (ретроспектива), проверка раз в 8ч не видит
  экстремумы внутри свечи; official maintenance tiers не подтверждены.
- AMZNX/AAPLX (токенизированные акции, xStocks) исключены из подтверждаемого
  результата: с 2026-09-03 действуют отдельные position rewards / corporate
  adjustments (long получает, short платит), которые не видны в публичном
  fundingRate — неучтённый риск.
- Итог: «слишком маленькая и недоказанная прибыль за слишком долгий срок
  при реальном риске потери». Запускать на деньги нельзя.

## Что проверено (публичные данные, без ордеров)

| Шаг | Что сделано | Артефакт |
|---|---|---|
| 0 | Документация: эндпоинты, лимиты (10 req/s IP), формат символов SPOT/PERP | reports/pionex_step01.md |
| 1 | Public probe: tickers/bookTickers/depth/klines/fundingRates/mark/index/OI; shortlist 25 рынков | scripts/pionex_public_probe.py, scripts/pionex_shortlist.py |
| 2 | Offline-экономика: строгая USDT-модель carry (q, basis B=F−S, funding=Position Value×Rate, 4 комиссии) | scripts/pionex_carry_v2.py, reports/pionex_step2b.md |
| 3 | Margin-survivability + cost audit: минимальный collateral, C_deployed, неперекрывающиеся окна | scripts/pionex_margin_audit.py, reports/pionex_step3.md |
| 4 | Финальная сверка: одна согласованная таблица AAVE/ARKM, E_max, даты окон, флаги unverified | scripts/pionex_final_check.py, reports/pionex_step4_final.md |
| — | Read-only ключ владельца (balances пусто, write отклонён AUTH_UNAVAILABLE) | scripts/pionex_key_check.py |

## Освоенная инфраструктура Pionex (полезно для будущего)

- API: https://api.pionex.com (публичный REST), WS — только forward.
- Доки в markdown: https://www.pionex.com/docs/llms.txt (полный индекс),
  любая страница доступна как *.md.
- Подпись приватных запросов (в доке описана неточно, подтверждено
  эмпирически): HMAC-SHA256 hex от `METHOD + PATH + '?' +
  QUERY(отсортированный по ключам, timestamp в query) [+ compact JSON body]`,
  headers PIONEX-KEY / PIONEX-SIGNATURE.
- endTime в klines НЕ может быть в будущем (MARKET_PARAMETER_ERROR).
- openInterests возвращает ВСЕ символы (параметр symbol игнорируется).
- AI Kit (MCP/Skills/CLI, github.com/pionex-official/pionex-ai-kit) —
  официальная инфраструктура для агентов; подключать после сверки с REST.

## Воспроизведение

```
python3 scripts/pionex_public_probe.py --max-markets 25   # шаги 0-1 (без ключа)
python3 scripts/pionex_carry_v2.py                        # шаг 2 (без ключа)
python3 scripts/pionex_margin_audit.py                    # шаг 3 (без ключа)
python3 scripts/pionex_final_check.py                     # шаг 4 (без ключа)
python3 scripts/pionex_key_check.py --key-file PATH       # проверка read-only ключа
```

Данные: data/pionex/*.json (снимки на 2026-10-06).
Предупреждение: скрипты обращаются к живому API (публичные эндпоинты),
соблюдают лимит 10 req/s; ордеров не создают.
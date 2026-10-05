# Финальный статус 44h — mk_18db19212772b000 (2026-10-05)

Окно: start 2026-10-03T18:41:46Z → cutoff 2026-10-05T14:41:46Z (44.0003h detail /
44.0h econ/opp), без скрытых допусков (event time в [start,cutoff] и receive <= cutoff).
bad_lines: depth=0, trades=0, oracle=0. Schema opportunity v3. Комиссии подтверждены
ранее (maker=taker=0.1%, бпс-модель использует их). Состояние стороны:
side_confirmed=False (источник подтверждения стороны не установлен),
decision_ready=False. Public opportunity НЕ является fill или прибылью.

| Метрика | PRLUSDT | QUANTUSUSDT | LTCUSDT |
|---|---|---|---|
| unique trades (в окне) | 21346 | 7678 | 922 |
| trades/h | 482.9 | 172.4 | 18.7 |
| notional/h, USDT | 56659 | 16852 | 410 |
| notional всего, USDT | 2 492 978 | 741 465 | 18 022 |
| spread p50, bps | 94.8 | 177.1 | 71.0 |
| spread p10/p90, bps | (детали в JSON) | (детали в JSON) | (детали в JSON) |
| ask depth cov $5/$10/$25, % | 100/100/100 | 100/100/100 | 100/100/100 |
| trade coverage | history_truncated | observed_window | observed_window |
| price matches ($5) | 18639 | 6352 | 532 |
| improved ($5) | 362 | 13498 | 14482 |
| queued ($5, очередь впереди) | 14122 | 984 | 0 |
| directed touches | 0 (side unverified) | 0 (side unverified) | 0 (side unverified) |
| exit full/partial/unobserved | 0 (гейт закрыт) | 0 (гейт закрыт) | 0 (гейт закрыт) |
| net PnL / markout | нет (гейт закрыт) | нет (гейт закрыт) | нет (гейт закрыт) |

Статусы:
- data_ready: true (окно 44h, bad_lines=0, 3 пары, coverage 100% по глубине,
  PRL требует оговорки history_truncated по trade-coverage)
- side_ready: false (side=unverified; taker_type отсутствует в обоих потоках,
  семантика поля side не подтверждена)
- economics_ready: false (направленный расчёт и PnL закрыты гейтом стороны)
- decision: insufficient_evidence (данные и глубина собраны, но без
  подтверждённой стороны агрессора направленные выводы невозможны)

Обоснование: PRL — высокая ликвидность (482.9 trades/h, спред 94.8 bps — узкий,
порядка ~10 тиков), почти все match-события уходят в очередь (14122/18639);
QUANTUS — спред вдвое шире (177.1 bps); LTC — минимальная активность.
При спреде ~1 tick на PRL улучшение цены на целый тик невозможно, доступно
только место в очереди. Учитывая отсутствие подтверждённой стороны агрессора,
ни одна пара не получает статус candidate.

Файлы (44h):
- reports/pair_screen_maker_mk_18db19212772b000_44h.json
- reports/econ_mk_18db19212772b000_44h.json
- reports/opp_mk_18db19212772b000_44h.json
- reports/maker_final_mk_18db19212772b000.md (этот отчёт)

Commit/push не выполнялись — по инструкции жду отдельного решения.
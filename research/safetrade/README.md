# SafeTrade Research — исследовательская система котировок

Три внешние спот-биржи (Binance, OKX, Bybit) + SafeTrade: одновременный сбор публичных
котировок и проверка — опережают ли внешние источники изменения цен SafeTrade, достигает
ли SafeTrade внешней цены, остаётся ли прибыль после расходов, помогает ли подтверждение 2/3.

**ВИРТУАЛЬНАЯ ТОРГОВЛЯ** — реальных ордеров не отправляется, ключи не запрашиваются.
Результаты — исследовательские гипотезы, не рекомендации.

## Установка

```bash
cd safetrade-research
python3 -m venv venv
./venv/bin/pip install -r requirements.txt   # см. список ниже
```

Зависимости (проверены 2026-10-02): ccxt, aiohttp, websockets, pandas, pyarrow, duckdb,
streamlit, plotly, pyyaml, pytest, pytest-asyncio.

```bash
./venv/bin/pip install ccxt aiohttp websockets pandas pyarrow duckdb streamlit plotly pyyaml pytest pytest-asyncio
```

## Команды

```bash
# Диагностика окружения и доступности бирж
./venv/bin/python cli.py doctor

# Список рынков выбранных бирж (REST)
./venv/bin/python cli.py discover --out data/analysis/markets.json

# Сбор котировок (все включённые рынки; --minutes — ограничение времени)
./venv/bin/python cli.py collect --minutes 60 --symbols BTCUSDT

# Replay-проверка событий (снапшот+дельта, sequence-gaps, valid)
./venv/bin/python cli.py replay --symbol BTCUSDT

# Полный анализ: mid-ряды, оракул, премия, lead-lag, тест достижения A
./venv/bin/python cli.py analyze --symbol BTCUSDT

# Paper-симуляция гипотез A/B/C (виртуально)
./venv/bin/python cli.py paper --symbol BTCUSDT

# Браузерная панель (Streamlit, localhost)
./venv/bin/python cli.py dashboard --port 8501
# → http://127.0.0.1:8501

# Тесты
./venv/bin/python -m pytest tests/ -q
```

## Долгий сбор (переживает сессии)

Скрипт `scripts/collector_watchdog.sh` держит коллектор живым (setsid + crontab):

```bash
*/5 * * * * /home/<user>/safetrade-research/scripts/collector_watchdog.sh
```

Безопасная остановка при нехватке диска (<3 ГБ) встроена.

## Структура

- `adapters/` — биржевые адаптеры (binance, okx, bybit, safetrade): discover_markets/stream/health
- `collector/` — asyncio-сбор, ограниченные очереди, единый писатель
- `storage/` — JSONL-ротация (raw), Parquet (нормализованные), SQLite WAL (state)
- `analysis/` — OrderBook (snapshot+delta), bbo-ряды, медиана источников, премия, lead-lag, replay-check
- `simulator/` — PaperSimulator: сигналы (2/3 подтверждение, порог шума MAD), экономический фильтр, taker/taker
- `dashboard/` — Streamlit-панель (5 вкладок, русский)
- `tests/` — 18 тестов (снапшот/дельта, VWAP, as-of без будущего, комиссии, no-trading)
- `config/config.yaml` — вся конфигурация без секретов
- `reports/` — отчёты: выбор источников, анализ, paper

## Выбор источников (Этап 1, 2026-10-02)

Топ-5 CoinGecko по spot volume: Binance, CoinUp, BTCC, Pionex, Tapbit. Из них пригоден
только Binance (CoinUp — Cloudflare challenge; BTCC — таймаут; Pionex — нет WS; Tapbit — DNS).
Замены из следующих мест, явно: **OKX**, **GATE резерв**, **Bybit**. Тройка:
Binance / OKX / Bybit (общие пары BTC/ETH/DOGE/LTC-USDT с SafeTrade). Детали:
`reports/exchange_selection.md`.

SafeTrade: REST `safetrade.com/api/v2/trade/public/*` (работает с Firefox UA),
WS `wss://safe.trade/api/v2/websocket/public` (флатки — нужны ретраи с Origin).

## Ограничения

- Медиана источников = «оракул», не будущая цена; медиана не исполнима.
- Тест достижения mid ≠ исполнимый тест VWAP — они разделены (A и B).
- Порог пригодности котировок и порог шума — в config.yaml, выбраны на development-данных.
- Разделение dev/validation/holdout — только хронологическое, embargo >= max H.
- Отрицательный результат — валидный результат.
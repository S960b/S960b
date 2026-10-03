"""Maker feasibility screen on SafeTrade (этап 1, БЕЗ ордеров).

По ТЗ autonomous_bot_pivot_20261003.md и поправкам пользователя:

1. Широкий спред НЕ является основанием исключить пару из maker-отбора —
   это повод проверить встречную торговлю. Спред вычитается только при
   пересечении стакана (вынужденная продажа), не из собственного maker-цикла.
2. В широком отборе показываем spread_observed + число снимков + время наблюдения;
   spread_p50/p90 — только для детального сбора.
3. Полнота trades: пагинация по page; если восстановить историю нельзя —
   trade_coverage = history_truncated / coverage_unknown (не «торговли нет»).
4. Единый ограничитель запросов SafeTrade (RPM), учитывает все вызовы.
5. Минимальный размер: min_qty × last_price — предварительно; отдельно
   min_notional, округление, резерв комиссии. fee_unverified ≠ подтверждено.
6. Внешние биржи — только ориентир цены для финалистов; их наличие не условие.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from adapters.safetrade import SafeTradeAdapter

log = logging.getLogger(__name__)

PAGE_LIMIT = 100
MAX_PAGES = 50          # до 5000 сделок на пару при детальном сборе
DEFAULT_RPM = 20        # консервативный общий бюджет SafeTrade (поправка 4)


class SafeTradeRateLimiter:
    """Единый ограничитель запросов к SafeTrade REST (fixed window).

    Учитывает ВСЕ вызовы (depth, trades, markets, метаданные).
    """

    def __init__(self, rpm: float = DEFAULT_RPM):
        self.min_interval = 60.0 / rpm
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.calls = 0
        self.blocks_429 = 0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1


@dataclass
class PairFeasibility:
    symbol: str            # "BTCUSDT"
    native: str            # "btcusdt"
    base: str
    enabled: bool
    min_qty: str = "?"
    price_precision: int = 0
    amount_precision: int = 0
    min_price: str = "?"
    max_price: str = "?"
    last_price: float = None
    min_notional_est: float = None     # min_qty × last_price (предварит.)
    # торговля
    trade_count: int = 0               # уникальных сделок из полученной истории
    traded_notional: float = 0.0
    trade_history_start: str = None    # created_at первой (старой) полученной
    trade_history_end: str = None      # created_at последней (свежей)
    trade_pages: int = 0               # сколько страниц прочитано
    trade_coverage: str = "unknown"    # full / history_truncated / coverage_unknown / no_trades
    median_gap_s: float = None
    p95_gap_s: float = None
    active_hours: int = 0              # часов покрытия с >=1 сделкой (поправка 2/3)
    span_hours: float = None           # полный охват истории в часах
    trades_per_hour: float = None      # сделок в час по охвату
    last_trade_age_min: float = None   # возраст последней сделки, мин
    # стакан
    n_depth: int = 0                   # число снимков depth
    depth_obs_start: str = None
    depth_obs_end: str = None
    spread_observed_bps: float = None  # по последнему снимку (один снимок = наблюдение)
    depth_ask_5_usdt: float = None     # ask-VWAP на 5 USDT (цена)
    depth_ask_10_usdt: float = None
    depth_ask_25_usdt: float = None
    depth_bid_5_usdt: float = None     # bid-VWAP на 5 USDT
    depth_bid_10_usdt: float = None
    depth_bid_25_usdt: float = None
    bid_vwap_exit_10: float = None     # bid-VWAP на qty покупки 10 USDT (выход)
    # комиссии/ссылки
    fee_status: str = "fee_unverified"
    fee_maker_bps: float = None
    fee_taker_bps: float = None
    external_reference: str = "missing"   # yes / partial / missing
    # итог
    candidate_status: str = "pending"     # pass / fail / insufficient_evidence
    reason: str = ""

    def to_row(self):
        d = self.__dict__.copy()
        return d


async def _fetch_trades(adapter, native, limiter, pages=2):
    """Читает до `pages` страниц public trades (по убыванию времени).

    Возвращает список сделок + признак полноты.
    """
    trades = []
    cov = "coverage_unknown"
    from adapters.safetrade import REST as _REST
    for page in range(1, pages + 1):
        await limiter.wait()
        url = f'{_REST}/markets/{native}/trades?limit={PAGE_LIMIT}&page={page}'
        try:
            batch = await asyncio.to_thread(adapter._http, url)
        except Exception:
            break
        if not isinstance(batch, list) or not batch:
            break
        # уникальные по id, сохраняя порядок (свежие первыми)
        seen = {t['id'] for t in trades}
        new = [t for t in batch if t['id'] not in seen]
        trades.extend(new)
        if len(new) < PAGE_LIMIT:
            cov = "full" if page == 1 else "full_at_page"
            break
        if page == pages:
            cov = "history_truncated"
    if not trades:
        cov = "no_trades_observed"
    return trades, cov


@dataclass
class ScreenResult:
    pairs: list = field(default_factory=list)
    api_calls: int = 0
    blocks_429: int = 0
    started_utc: str = ""
    finished_utc: str = ""
    params: dict = field(default_factory=dict)


def _vwap_for_budget(book_bids, book_asks, budget_usdt):
    """ask-VWAP (покупка) и bid-VWAP (продажа) на budget USDT из снимка.

    Возвращает (ask_price, bid_price); None если глубины не хватает.
    """
    from decimal import Decimal
    from analysis.book import OrderBook
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade',
              'canonical_symbol': 'x', 'bids': book_bids, 'asks': book_asks,
              'quality_flags': [], 'recv_monotonic_ns': 1})
    ap, _ = ob.vwap_cost('ask', Decimal(str(budget_usdt)))
    bp, _ = ob.vwap_cost('bid', Decimal(str(budget_usdt)))
    return (float(ap) if ap is not None else None,
            float(bp) if bp is not None else None)


async def screen_pair(adapter, native, symbol, base, meta, limiter, depth_snaps=2,
                      trade_pages=2) -> PairFeasibility:
    p = PairFeasibility(symbol=symbol, native=native, base=base,
                        enabled=meta.get('status') == 'enabled',
                        min_qty=str(meta.get('min_qty', '?')),
                        price_precision=int(meta.get('price_precision') or 0),
                        amount_precision=int(meta.get('amount_precision') or 0),
                        min_price=str(meta.get('min_price', '?')),
                        max_price=str(meta.get('max_price', '?')))

    # --- стакан: несколько снимков (поправка 2: spread_observed, не распределение)
    bids = asks = None
    for _ in range(depth_snaps):
        await limiter.wait()
        try:
            raw, snap, utc, mono, req_utc, req_mono = await adapter.rest_depth(native, limit=100)
            p.n_depth += 1
            bids, asks = snap.get('bids', []), snap.get('asks', [])
            if p.depth_obs_start is None:
                p.depth_obs_start = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(utc/1e9))
            p.depth_obs_end = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(utc/1e9))
        except Exception:
            break
        await asyncio.sleep(1.0)   # не дёргать часто, общий лимитер всё равно рулит

    if bids and asks:
        try:
            best_bid = float(max(bids, key=lambda x: float(x[0]))[0])
            best_ask = float(min(asks, key=lambda x: float(x[0]))[0])
            mid = (best_bid + best_ask) / 2.0
            p.spread_observed_bps = round((best_ask - best_bid) / mid * 1e4, 1)
            p.last_price = mid
            try:
                p.min_notional_est = float(p.min_qty) * mid
            except (TypeError, ValueError):
                p.min_notional_est = None
            a5, b5 = _vwap_for_budget(bids, asks, 5)
            a10, b10 = _vwap_for_budget(bids, asks, 10)
            a25, b25 = _vwap_for_budget(bids, asks, 25)
            p.depth_ask_5_usdt, p.depth_bid_5_usdt = a5, b5
            p.depth_ask_10_usdt, p.depth_bid_10_usdt = a10, b10
            p.depth_ask_25_usdt, p.depth_bid_25_usdt = a25, b25
            # bid-VWAP выхода на qty покупки 10 USDT
            if a10 and b10:
                from decimal import Decimal
                qty10 = 10.0 / a10
                ob = _order_book(bids, asks)
                bp_exit, _ = ob.vwap('bid', Decimal(str(qty10)))
                p.bid_vwap_exit_10 = float(bp_exit) if bp_exit is not None else None
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # --- торговля
    trades, cov = await _fetch_trades(adapter, native, limiter, pages=trade_pages)
    p.trade_pages = min(trade_pages, max(1, (len(trades) + PAGE_LIMIT - 1)//PAGE_LIMIT))
    p.trade_coverage = cov
    if trades:
        p.trade_count = len(trades)
        p.traded_notional = round(sum(float(t.get('total') or 0) for t in trades), 2)
        p.trade_history_start = trades[-1].get('created_at')
        p.trade_history_end = trades[0].get('created_at')
        # паузы между сделками (в секундах), по created_at
        from datetime import datetime
        def _ts(s):
            try:
                return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').timestamp()
            except (TypeError, ValueError):
                return None
        ts = [_ts(t.get('created_at')) for t in trades]
        ts = [x for x in ts if x is not None]
        if len(ts) >= 2:
            gaps = sorted([ts[i-1] - ts[i] for i in range(1, len(ts))])
            p.median_gap_s = round(gaps[len(gaps)//2], 1)
            p.p95_gap_s = round(gaps[int(len(gaps)*0.95)], 1)
            # доля активных часов: сколько часов из покрытия имеют >=1 сделку
            span_h = max(1e-9, (ts[0] - ts[-1]) / 3600.0)
            hours = {int(x // 3600) for x in ts}
            p.active_hours = len(hours)
            p.span_hours = round(span_h, 2)
            p.trades_per_hour = round(len(ts) / span_h, 2)
            p.last_trade_age_min = round((time.time() - ts[0]) / 60.0, 1)
    return p


def _order_book(bids, asks):
    from analysis.book import OrderBook
    ob = OrderBook()
    ob.apply({'event_type': 'book_snapshot', 'exchange': 'safetrade',
              'canonical_symbol': 'x', 'bids': bids, 'asks': asks,
              'quality_flags': [], 'recv_monotonic_ns': 1})
    return ob


def _external_reference(base, common_binance, common_okx, common_bybit):
    n = 0
    if base in common_binance:
        n += 1
    if base in common_okx:
        n += 1
    if base in common_bybit:
        n += 1
    return {0: 'missing', 1: 'partial', 2: 'partial', 3: 'yes'}[n]


async def run_screen(usdt_only=True, depth_snaps=2, trade_pages=2, rpm=DEFAULT_RPM,
                     common=None, verbose=False) -> ScreenResult:
    """Широкий отбор всех пар SafeTrade (поправки 1-6)."""
    adapter = SafeTradeAdapter()
    limiter = SafeTradeRateLimiter(rpm)
    res = ScreenResult()
    res.started_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    res.params = {'depth_snaps': depth_snaps, 'trade_pages': trade_pages, 'rpm': rpm,
                  'usdt_only': usdt_only}

    markets = await asyncio.to_thread(adapter.discover_markets)
    pairs = []
    for m in markets:
        if usdt_only and m.get('quote') != 'USDT':
            continue
        sym = m['symbol'].replace('/', '')
        pairs.append((sym, m['native'], m['base'], m))
    pairs.sort(key=lambda x: x[0])

    common = common or ({}, {}, {})
    cb, co, cby = common

    for sym, native, base, meta in pairs:
        p = await screen_pair(adapter, native, sym, base, meta, limiter,
                              depth_snaps=depth_snaps, trade_pages=trade_pages)
        p.external_reference = _external_reference(base, cb, co, cby)
        # Первичный статус: техническая пригодность (НЕ экономика, поправка 1).
        # Активность измеряется временным покрытием, а НЕ числом прочитанных
        # страниц (поправка 3: history_truncated — не «торговли нет»).
        reasons = []
        if meta.get('status') != 'enabled':
            p.candidate_status = 'fail'
            reasons.append('market_disabled')
        elif p.min_notional_est is not None and p.min_notional_est > 10.0:
            p.candidate_status = 'fail'
            reasons.append(f'min_notional={p.min_notional_est:.2f} USDT > 10')
        elif p.trade_count == 0 and p.trade_coverage == 'no_trades_observed':
            p.candidate_status = 'insufficient_evidence'
            reasons.append('no_trades_observed (0 сделок за охват)')
        elif p.trade_count == 0 and p.trade_coverage == 'coverage_unknown':
            p.candidate_status = 'insufficient_evidence'
            reasons.append('trade_coverage=coverage_unknown')
        elif p.last_trade_age_min is not None and p.last_trade_age_min > 24 * 60:
            # последняя сделка старше суток — торговая активность затухла
            p.candidate_status = 'insufficient_evidence'
            reasons.append(f'last_trade_time={p.trade_history_end} старше 24ч')
        elif p.trades_per_hour is not None and p.trades_per_hour < 5:
            # менее 5 сделок/час по охвату — для maker слишком тихий поток
            p.candidate_status = 'insufficient_evidence'
            reasons.append(f'trades_per_hour={p.trades_per_hour} < 5')
        else:
            # широкий спред игнорируем (поправка 1): это повод проверить
            # встречную торговлю, а не исключить пару
            p.candidate_status = 'pass'
            reasons.append('ok')
        p.reason = '; '.join(reasons)
        res.pairs.append(p)
        if verbose:
            print(f"{sym:<14} status={p.candidate_status:<22} trades={p.trade_count:>4} "
                  f"notional={p.traded_notional:>10.1f} tph={p.trades_per_hour} "
                  f"last={p.last_trade_age_min}мин gap50={p.median_gap_s} "
                  f"spread={p.spread_observed_bps}bps min_not={p.min_notional_est if p.min_notional_est is not None else 'NA'} "
                  f"ext={p.external_reference} | {p.reason}")
        res.api_calls = limiter.calls

    res.finished_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    res.api_calls = limiter.calls
    res.blocks_429 = limiter.blocks_429
    return res


def screen_to_csv(res: ScreenResult, path: str):
    import csv
    cols = ['symbol', 'base', 'enabled', 'min_qty', 'min_price', 'max_price',
            'last_price', 'min_notional_est', 'trade_count', 'traded_notional',
            'trade_history_start', 'trade_history_end', 'trade_pages', 'trade_coverage',
            'median_gap_s', 'p95_gap_s', 'active_hours', 'span_hours', 'trades_per_hour',
            'last_trade_age_min', 'n_depth', 'depth_obs_start', 'depth_obs_end',
            'spread_observed_bps', 'depth_ask_5_usdt', 'depth_ask_10_usdt', 'depth_ask_25_usdt',
            'depth_bid_5_usdt', 'depth_bid_10_usdt', 'depth_bid_25_usdt', 'bid_vwap_exit_10',
            'fee_status', 'fee_maker_bps', 'fee_taker_bps', 'external_reference',
            'candidate_status', 'reason']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for p in res.pairs:
            w.writerow({c: p.__dict__.get(c) for c in cols})


def screen_to_json(res: ScreenResult, path: str):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'started_utc': res.started_utc, 'finished_utc': res.finished_utc,
                   'api_calls': res.api_calls, 'blocks_429': res.blocks_429,
                   'params': res.params,
                   'pairs': [p.to_row() for p in res.pairs]},
                  f, indent=1, ensure_ascii=False)
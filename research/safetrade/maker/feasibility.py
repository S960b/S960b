"""Maker feasibility screen on SafeTrade (этап 1, БЕЗ ордеров).

Ревью 738de3a применено:
- P0.3: timestamps разбираются строго как aware UTC (дробные секунды) —
  strptime('...Z').timestamp() трактовался как локальное время (ошибка -10800с).
- P0.4: VWAP возвращает цену И покрытие (covered_qty/quote, target, status);
  частичная глубина не выдаётся за полное покрытие бюджета; выход — на
  фактически купленное qty.
- P0.5: общий HTTP wrapper (status/429/timeout/parse), request_failed отделён
  от no_trades; повтор страницы/перекрытие не дают false full_at_page;
  limiter считает discover_markets; blocks_429 — реальный счётчик.
- P0.6: pass требует валидного двустороннего стакана, известного min_notional,
  разобранных timestamps и измеренной активности; недостаток данных =>
  insufficient_evidence с причинами, не implicit pass.

По ТЗ autonomous_bot_pivot_20261003.md:
1. Широкий спред НЕ исключает пару — повод проверить встречную торговлю.
2. В широком отборе — spread_observed + число снимков + время наблюдения.
3. Полнота trades: пагинация по page с дочитыванием до перекрытия.
4. Единый ограничитель запросов SafeTrade.
5. Минимальный размер: min_qty × last_price — предварительно; fee_unverified.
6. Внешние биржи — только ориентир для финалистов; наличие не условие.
"""
import asyncio
import json
import logging
import time
import urllib.error
from dataclasses import dataclass, field

from adapters.safetrade import SafeTradeAdapter

from maker.util import parse_iso_utc, age_minutes, valid_book, coverage_note

log = logging.getLogger(__name__)

PAGE_LIMIT = 100
MAX_PAGES = 50          # до 5000 сделок на пару при детальном сборе
DEFAULT_RPM = 20        # консервативный общий бюджет SafeTrade (поправка 4)
MIN_TPH = 5.0           # порог активности ПЕРВИЧНОГО отбора (настройка, не доказанный минимум)
BUDGET_USDT = 10.0      # бюджет предполагаемой заявки для min_notional / покрытия


class SafeTradeRateLimiter:
    """Единый ограничитель запросов к SafeTrade REST (fixed window).

    Учитывает ВСЕ вызовы (depth, trades, markets, метаданные)."""

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

    async def __aenter__(self):
        await self.wait()
        return self

    async def __aexit__(self, *exc):
        return False


async def safe_http(adapter, url, limiter, attempts=3):
    """HTTP wrapper (P0.5): учёт лимитера, 429-бэкофф, timeout/parse-ошибки.

    Возвращает {'ok': bool, 'status': int|None, 'data': ..., 'error': str|None,
                'blocked_429': bool}.
    """
    import urllib.request
    from adapters.safetrade import UA as _UA
    last_err = None
    status = None
    blocked = False
    for attempt in range(attempts):
        await limiter.wait()
        req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                status = r.status
                body = r.read().decode('utf-8')
            try:
                return {'ok': True, 'status': status, 'data': json.loads(body),
                        'error': None, 'blocked_429': blocked}
            except ValueError as e:
                return {'ok': False, 'status': status, 'data': None,
                        'error': f'parse_error: {e}', 'blocked_429': blocked}
        except urllib.error.HTTPError as e:
            status = e.code
            if e.code == 429:
                limiter.blocks_429 += 1
                blocked = True
                await asyncio.sleep(2.0 * (attempt + 1))   # backoff (Retry-After не гарантирован)
                continue
            last_err = f'HTTP {e.code}'
            break
        except Exception as e:
            last_err = f'{type(e).__name__}: {str(e)[:100]}'
            await asyncio.sleep(1.0)
    return {'ok': False, 'status': status, 'data': None, 'error': last_err,
            'blocked_429': blocked}


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
    trade_count: int = 0               # уникальных сделок (pair,id)
    traded_notional: float = 0.0
    trade_history_start: str = None    # created_at первой (старой) полученной
    trade_history_end: str = None      # created_at последней (свежей)
    trade_pages: int = 0               # сколько страниц прочитано
    trade_coverage: str = "coverage_unknown"  # full/history_truncated/no_trades/request_failed...
    median_gap_s: float = None
    p95_gap_s: float = None
    active_hours: int = 0
    span_hours: float = None
    trades_per_hour: float = None
    last_trade_age_min: float = None
    # стакан (только валидные снимки)
    n_depth: int = 0
    n_depth_valid: int = 0
    depth_obs_start: str = None
    depth_obs_end: str = None
    spread_observed_bps: float = None   # по последнему ВАЛИДНОМУ снимку
    # цены VWAP по бюджетам; coverage: full/partial/none
    depth_ask_5_usdt: float = None
    depth_ask_5_cov: str = None
    depth_bid_5_usdt: float = None
    depth_bid_5_cov: str = None
    depth_ask_10_usdt: float = None
    depth_ask_10_cov: str = None
    depth_bid_10_usdt: float = None
    depth_bid_10_cov: str = None
    depth_ask_25_usdt: float = None
    depth_ask_25_cov: str = None
    depth_bid_25_usdt: float = None
    depth_bid_25_cov: str = None
    bid_vwap_exit_10: float = None      # выход на фактически купленное qty
    bid_vwap_exit_10_cov: str = None
    # комиссии/ссылки
    fee_status: str = "fee_unverified"
    fee_maker_bps: float = None
    fee_taker_bps: float = None
    external_reference: str = "missing"   # yes / partial / missing
    # итог
    candidate_status: str = "pending"
    reason: str = ""

    def to_row(self):
        return self.__dict__.copy()


async def _http_observed(adapter, url, limiter, attempts=3):
    """Единая HTTP-обёртка SafeTrade поверх adapter._http (который уже умеет UA).

    - Учитывает limiter (все вызовы через один бюджет, P0.5).
    - 429 => счётчик, backoff, ретрай.
    - Любая ошибка запроса/парсинга отделяется от пустого валидного ответа.
    Возвращает {'ok': bool, 'data': ...|None, 'error': str|None}.
    """
    last_err = None
    for attempt in range(attempts):
        await limiter.wait()
        try:
            data = await asyncio.to_thread(adapter._http, url)
            return {'ok': True, 'data': data, 'error': None}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                limiter.blocks_429 += 1
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            last_err = f'HTTP {e.code}'
            break
        except Exception as e:
            last_err = f'{type(e).__name__}: {str(e)[:100]}'
            await asyncio.sleep(1.0)
    return {'ok': False, 'data': None, 'error': last_err}


async def _fetch_trades(adapter, native, limiter, pages=2):
    """Читает public trades по страницам до перекрытия с ранее виденными ID.

    Возвращает (trades, cov, request_failed). Недостаток новых ID на странице
    НЕ равен концу истории: страница полностью из уже виденных = full;
    частичное перекрытие не останавливает; max_pages => history_truncated.
    """
    from adapters.safetrade import REST as _REST
    trades = []
    seen = set()
    cov = "coverage_unknown"
    request_failed = False
    for page in range(1, pages + 1):
        url = f'{_REST}/markets/{native}/trades?limit={PAGE_LIMIT}&page={page}'
        r = await _http_observed(adapter, url, limiter)
        if not r['ok']:
            if page == 1:
                cov = "request_failed"
                request_failed = True
                break
            cov = "history_truncated"   # дочитали, потом соединение упало
            break
        batch = r['data']
        if not isinstance(batch, list):
            cov = "coverage_unknown"
            break
        if not batch:
            cov = "full" if page == 1 else "full_at_page"   # пусто = конец истории
            break
        new = [t for t in batch if isinstance(t, dict) and 'id' in t and t['id'] not in seen]
        if not new:
            # повтор страницы: пагинация не продвигается => НЕ конец истории (P0.1 0b71d28)
            cov = "pagination_not_advancing"
            break
        for t in new:
            seen.add(t['id'])
        trades.extend(new)
        if len(new) < PAGE_LIMIT:
            cov = "full" if page == 1 else "full_at_page"
            break
        if page == pages:
            cov = "history_truncated"
    return trades, cov, request_failed


@dataclass
class ScreenResult:
    pairs: list = field(default_factory=list)
    api_calls: int = 0
    blocks_429: int = 0
    started_utc: str = ""
    finished_utc: str = ""
    params: dict = field(default_factory=dict)


def _vwap_coverage(ob, side, budget_usdt):
    """ask/bid VWAP на budget USDT из OrderBook с покрытием.

    Возвращает (price, covered_quote, coverage): coverage in full/partial/none.
    Полное покрытие — когда потрачен весь бюджет (cost >= budget-tolerance).
    """
    from decimal import Decimal
    price, qty = ob.vwap_cost(side, Decimal(str(budget_usdt)))
    if price is None:
        return None, 0.0, "none"
    cost = float(qty) * float(price)
    tol = max(1e-6, float(budget_usdt) * 1e-9)
    if cost >= float(budget_usdt) - tol:
        return float(price), cost, "full"
    return float(price), cost, "partial"


def _vwap_for_budget(book_bids, book_asks, budget_usdt):
    """ask/bid VWAP на budget USDT с покрытием (P0.4).

    Возвращает dict: {'ask_price','ask_quote','ask_cov','bid_price','bid_quote','bid_cov'}.
    """
    ob = _order_book(book_bids, book_asks)
    ap, aq, acov = _vwap_coverage(ob, 'ask', budget_usdt)
    bp, bq, bcov = _vwap_coverage(ob, 'bid', budget_usdt)
    return {'ask_price': ap, 'ask_quote': aq, 'ask_cov': acov,
            'bid_price': bp, 'bid_quote': bq, 'bid_cov': bcov}


async def screen_pair(adapter, native, symbol, base, meta, limiter, depth_snaps=2,
                      trade_pages=2) -> PairFeasibility:
    p = PairFeasibility(symbol=symbol, native=native, base=base,
                        enabled=meta.get('status') == 'enabled',
                        min_qty=str(meta.get('min_qty', '?')),
                        price_precision=int(meta.get('price_precision') or 0),
                        amount_precision=int(meta.get('amount_precision') or 0),
                        min_price=str(meta.get('min_price', '?')),
                        max_price=str(meta.get('max_price', '?')))

    # --- стакан: валидные снимки (P0.6: n_depth_valid), последний валидный — для цен
    bids = asks = None
    last_valid = None
    for _ in range(depth_snaps):
        await limiter.wait()
        try:
            raw, snap, utc, mono, req_utc, req_mono = await adapter.rest_depth(native, limit=100)
            p.n_depth += 1
            b, a = snap.get('bids', []), snap.get('asks', [])
            ok, bb, ba = valid_book(b, a)
            if p.depth_obs_start is None:
                p.depth_obs_start = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(utc/1e9))
            p.depth_obs_end = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(utc/1e9))
            if ok:
                p.n_depth_valid += 1
                bids, asks = b, a
                last_valid = (bb, ba)
        except Exception:
            break
        await asyncio.sleep(1.0)

    if last_valid is not None:
        bb, ba = last_valid
        mid = (bb + ba) / 2.0
        p.spread_observed_bps = round((ba - bb) / mid * 1e4, 1)
        p.last_price = mid
        try:
            p.min_notional_est = float(p.min_qty) * mid
        except (TypeError, ValueError):
            p.min_notional_est = None
        for b_usdt in (5, 10, 25):
            v = _vwap_for_budget(bids, asks, b_usdt)
            setattr(p, f'depth_ask_{b_usdt}_usdt', v['ask_price'])
            setattr(p, f'depth_ask_{b_usdt}_cov', v['ask_cov'])
            setattr(p, f'depth_bid_{b_usdt}_usdt', v['bid_price'])
            setattr(p, f'depth_bid_{b_usdt}_cov', v['bid_cov'])
        # bid-VWAP выхода на фактически купленное qty (10 USDT по ask)
        av = _vwap_for_budget(bids, asks, 10)
        if av['ask_price'] and av['ask_cov'] == 'full':
            from decimal import Decimal
            qty_bought = Decimal('10') / Decimal(str(av['ask_price']))
            ob = _order_book(bids, asks)
            bp_exit, qty_sold = ob.vwap('bid', qty_bought)
            if bp_exit is not None:
                p.bid_vwap_exit_10 = float(bp_exit)
                # full только если проданное >= купленного (допуск округления 1e-6)
                tol = Decimal(str(float(qty_sold) * 1e-6))
                if Decimal(str(float(qty_sold))) + tol >= qty_bought:
                    p.bid_vwap_exit_10_cov = 'full'
                else:
                    p.bid_vwap_exit_10_cov = 'partial'
            else:
                p.bid_vwap_exit_10_cov = 'none'
        elif av['ask_price'] is not None:
            p.bid_vwap_exit_10_cov = 'partial'

    # --- торговля (P0.3/P0.5)
    trades, cov, failed = await _fetch_trades(adapter, native, limiter, pages=trade_pages)
    p.trade_pages = min(trade_pages, max(1, (len(trades) + PAGE_LIMIT - 1)//PAGE_LIMIT))
    p.trade_coverage = cov
    if failed:
        p.candidate_status = 'insufficient_evidence'
        p.reason = 'trade_request_failed'
        return p

    p.trade_count = len(trades)
    p.traded_notional = round(sum(float(t.get('total') or 0) for t in trades), 2)
    if trades:
        p.trade_history_start = trades[-1].get('created_at')
        p.trade_history_end = trades[0].get('created_at')
        ts = [parse_iso_utc(t.get('created_at')) for t in trades]
        ts = [x for x in ts if x is not None]
        parse_fail = sum(1 for t in trades if parse_iso_utc(t.get('created_at')) is None)
        p.reason += f'; unparsed_ts={parse_fail}' if parse_fail else ''
        if len(ts) >= 2:
            ts_sorted = sorted(ts, reverse=True)
            gaps = sorted([ts_sorted[i-1] - ts_sorted[i] for i in range(1, len(ts_sorted))])
            p.median_gap_s = round(gaps[len(gaps)//2], 1)
            p.p95_gap_s = round(gaps[int(len(gaps)*0.95)], 1)
            span_h = max(1e-9, (ts_sorted[0] - ts_sorted[-1]) / 3600.0)
            hours = {int(x // 3600) for x in ts_sorted}
            p.active_hours = len(hours)
            p.span_hours = round(span_h, 2)
            p.trades_per_hour = round(len(ts_sorted) / span_h, 2)
        p.last_trade_age_min = round((time.time() - ts[0]) / 60.0, 1) if ts else None
    elif cov in ('no_trades_observed',):
        p.reason += '; no_trades_in_window'
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
                     common=None, verbose=False, budget_usdt=BUDGET_USDT,
                     min_tph=MIN_TPH) -> ScreenResult:
    """Широкий отбор всех пар SafeTrade (поправки 1-6 + P0.6)."""
    adapter = SafeTradeAdapter()
    limiter = SafeTradeRateLimiter(rpm)
    res = ScreenResult()
    res.started_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    res.params = {'depth_snaps': depth_snaps, 'trade_pages': trade_pages, 'rpm': rpm,
                  'usdt_only': usdt_only, 'budget_usdt': budget_usdt, 'min_tph': min_tph}

    # discover_markets — ТОЖЕ через limiter (P0.5: «все» вызовы)
    await limiter.wait()
    markets = await asyncio.to_thread(adapter.discover_markets)
    pairs = []
    for m in markets:
        if usdt_only and m.get('quote') != 'USDT':
            continue
        pairs.append((m['symbol'].replace('/', ''), m['native'], m['base'], m))
    pairs.sort(key=lambda x: x[0])

    common = common or ({}, {}, {})
    cb, co, cby = common

    for sym, native, base, meta in pairs:
        if meta.get('status') != 'enabled':
            # disabled пары не опрашиваем как кандидатов (P0.6)
            p = PairFeasibility(symbol=sym, native=native, base=base, enabled=False,
                                min_qty=str(meta.get('min_qty', '?')),
                                candidate_status='fail', reason='market_disabled')
            res.pairs.append(p)
            continue
        p = await screen_pair(adapter, native, sym, base, meta, limiter,
                              depth_snaps=depth_snaps, trade_pages=trade_pages)
        p.external_reference = _external_reference(base, cb, co, cby)
        _classify(p, budget_usdt=budget_usdt, min_tph=min_tph)
        res.pairs.append(p)
        if verbose:
            print(f"{sym:<14} status={p.candidate_status:<22} trades={p.trade_count:>4} "
                  f"tph={p.trades_per_hour} last={p.last_trade_age_min}мин "
                  f"depth={p.n_depth_valid}/{p.n_depth} spread={p.spread_observed_bps}bps "
                  f"min_not={p.min_notional_est if p.min_notional_est is not None else 'NA'} "
                  f"cov={p.trade_coverage} ext={p.external_reference} | {p.reason}")

    res.finished_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    res.api_calls = limiter.calls
    res.blocks_429 = limiter.blocks_429
    return res


def _classify(p: PairFeasibility, budget_usdt=BUDGET_USDT, min_tph=MIN_TPH):
    """P0.6 (+0b71d28): pass требует валидного двустороннего стакана, известных
    размеров, разобранных timestamps и ИЗМЕРЕННОЙ активности. trade_count==0
    не может дать pass даже при coverage=full (пустой рынок не кандидат);
    неизвестная активность — insufficient_evidence."""
    reasons = []
    if p.candidate_status == 'fail':
        return
    if p.candidate_status == 'insufficient_evidence' and p.reason.startswith('trade_request_failed'):
        return
    if p.n_depth_valid == 0:
        reasons.append(f'depth_invalid_or_missing (n={p.n_depth})')
    if p.min_notional_est is None:
        reasons.append('min_notional_unknown')
    elif p.min_notional_est > budget_usdt:
        reasons.append(f'min_notional={p.min_notional_est:.2f} USDT > budget {budget_usdt}')
    if p.trade_count == 0:
        # подтверждённое отсутствие сделок или 0 записей: не кандидат
        if p.trade_coverage == 'no_trades_observed':
            reasons.append('no_trades_observed')
        elif p.trade_coverage == 'request_failed':
            reasons.append('trade_request_failed')
        else:
            reasons.append(f'trade_count=0 (coverage={p.trade_coverage})')
    if p.trade_coverage in ('coverage_unknown', 'history_truncated', 'pagination_not_advancing'):
        reasons.append(f'trade_coverage={p.trade_coverage}')
    if p.trade_count and p.last_trade_age_min is None:
        reasons.append('trade_timestamps_unparsed')
    if p.last_trade_age_min is not None and p.last_trade_age_min > 24 * 60:
        reasons.append(f'last_trade_older_than_24h ({p.trade_history_end})')
    if p.trade_count and p.trades_per_hour is None:
        reasons.append('activity_unmeasured')
    if p.trades_per_hour is not None and p.trades_per_hour < min_tph:
        reasons.append(f'trades_per_hour={p.trades_per_hour} < {min_tph}')

    if not reasons:
        p.candidate_status = 'pass'
        p.reason = 'ok'
    else:
        p.candidate_status = 'insufficient_evidence' if p.trade_count else 'fail'
        p.reason = '; '.join(reasons)


def screen_to_csv(res: ScreenResult, path: str):
    import csv
    cols = ['symbol', 'base', 'enabled', 'min_qty', 'min_price', 'max_price',
            'last_price', 'min_notional_est', 'trade_count', 'traded_notional',
            'trade_history_start', 'trade_history_end', 'trade_pages', 'trade_coverage',
            'median_gap_s', 'p95_gap_s', 'active_hours', 'span_hours', 'trades_per_hour',
            'last_trade_age_min', 'n_depth', 'n_depth_valid', 'depth_obs_start', 'depth_obs_end',
            'spread_observed_bps', 'depth_ask_5_usdt', 'depth_ask_5_cov', 'depth_bid_5_usdt', 'depth_bid_5_cov',
            'depth_ask_10_usdt', 'depth_ask_10_cov', 'depth_bid_10_usdt', 'depth_bid_10_cov',
            'depth_ask_25_usdt', 'depth_ask_25_cov', 'depth_bid_25_usdt', 'depth_bid_25_cov',
            'bid_vwap_exit_10', 'bid_vwap_exit_10_cov',
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
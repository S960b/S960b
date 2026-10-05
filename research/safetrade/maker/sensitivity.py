"""Сценарный sensitivity-анализ maker-цитаты (шаг 3 ревью, 2026-10-05).

Отдельный исследовательский модуль; основной opportunity v3 НЕ изменяет.
Моделирует ВИРТУАЛЬНЫЙ портфель 100 USDT (50% USDT + 50% base), long-only
round-trips: каждая сделка начинается maker-покупкой на уровне bid и
закрывается maker-продажей на уровне ask (или принудительным taker-выходом).

Гипотезы:
  H1: public side = сторона taker (агрессора):
      public 'sell' = маркет-продажа -> заполняет наш resting BUY на bid;
      public 'buy'  = маркет-покупка -> заполняет наш resting SELL на ask.
  H2: инверсия (public side = сторона пассивной заявки).
  В одном сценарии одна сделка имеет ОДНО направление (взаимоисключающие).

Правила (по ТЗ шага 3):
- PRL: только присоединение к лучшему bid/ask (свободного тика нет).
  QUANTUS: join BBO для всех сценариев; improve=True — улучшение на 1 тик,
  только когда цена не пересекает противоположную сторону (не crossed).
- Размер заявки 5/10/25 USDT номинала; капитал без займа и без отрицательных
  остатков; остаток заявки и позиции переносятся между сделками.
- Очередь впереди 0x/1x/2x видимого объёма уровня на момент постановки.
  FIFO: маркет-объём сначала гасит очередь, затем нашу заявку. Уменьшение
  уровня стакана без подтверждённой сделки НЕ исполняет нашу заявку.
- Задержка активации 0/1/5 с: решение использует только данные, известные
  к моменту постановки; снимок стакана старше допустимого возраста (30с)
  помечается unobserved и не заменяется будущим.
- Комиссия 0,1% на каждое исполнение. В конце окна и при удержании >30 мин
  остаток закрывается taker по доступной противоположной стороне стакана с
  комиссией; недостающая глубина -> unrealized (не считается проданной).
- Метрики: net_pnl, return_on_100, n_fills, partial_fills, partial_share,
  turnover, max_position, max_drawdown, holding_time, markout_5/30/60 (None,
  если горизонт недоступен), unrealized_qty.
- PRL history_truncated переносится в метаданные; неполный ранний участок
  исключается из основного окна сравнения и показывается отдельной
  чувствительностью.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

FEE_RATE = Decimal('0.001')
MAX_HOLD_S = 30 * 60
STALE_S = 30.0
D6 = Decimal('0.000001')
D4 = Decimal('0.0001')


def _d(x) -> Decimal:
    return Decimal(str(x))


@dataclass
class Level:
    price: Decimal
    qty: Decimal

    @staticmethod
    def best(levels, want_max: bool) -> 'Level | None':
        return levels[0] if levels else None


@dataclass
class Book:
    ts: float
    bids: list
    asks: list

    @property
    def best_bid(self):
        return Level.best(self.bids, True)

    @property
    def best_ask(self):
        return Level.best(self.asks, False)

    @property
    def mid(self):
        bb, ba = self.best_bid, self.best_ask
        return (bb.price + ba.price) / 2 if bb and ba else None


@dataclass
class FillRec:
    ts: float
    side: str
    price: Decimal
    qty: Decimal
    fee: Decimal
    partial: bool
    taker: bool = False
    reason: str = ''


def parse_book(row: dict) -> Book | None:
    b, a = row.get('bids'), row.get('asks')
    if not isinstance(b, list) or not isinstance(a, list) or not b or not a:
        return None
    try:
        bids = sorted((Level(_d(p), _d(q)) for p, q in b),
                      key=lambda lv: lv.price, reverse=True)
        asks = sorted((Level(_d(p), _d(q)) for p, q in a),
                      key=lambda lv: lv.price)
    except (TypeError, ValueError):
        return None
    if bids[0].price >= asks[0].price:
        return None
    return Book(float(row.get('t', 0)) / 1e9, bids, asks)


def _parse_iso(s):
    from maker.util import parse_iso_utc
    ts = parse_iso_utc(s)
    return ts if ts is not None and ts > 0 else None


class Sensitivity:
    """Один round-trip прогон: pair x hypothesis x queue x delay x budget.

    Порядок: on_book для каждого снимка, on_trade для каждой сделки.
    """

    def __init__(self, pair: str, hypothesis: str, queue_factor: str,
                 delay_s: float, budget: Decimal, improve: bool = False,
                 tick: Decimal = Decimal('0.01'), capital: Decimal = Decimal('100'),
                 run_dir: str = '', window_start: float = 0.0,
                 window_end: float = 0.0, baseline: bool = False):
        self.pair = pair.upper()
        self.h = hypothesis                 # 'H1' | 'H2'
        self.baseline = baseline            # no-trade control
        self.qf = {'0x': Decimal('0'), '1x': Decimal('1'),
                   '2x': Decimal('2')}[queue_factor]
        self.delay_s = delay_s
        self.budget = budget
        self.improve = improve
        self.tick = tick
        self.capital = capital
        self.run_dir = run_dir
        self.window_start = window_start
        self.window_end = window_end

        self.cash = capital / 2
        self.base = Decimal('0')
        self.seed_mid: Decimal | None = None
        self.last_mid: Decimal | None = None
        self.begin_ts: float | None = None
        self.entry_ts: float | None = None   # момент входа (для hold timeout)

        self.order = None                   # активная entry (buy)
        self.exit_order = None              # maker-exit (sell)
        self.order_active_ts = 0.0          # когда заявка стала активной (delay)
        self.exit_active_ts = 0.0
        self.fills: list[FillRec] = []
        self.equity: list = []
        self.skipped_stale = 0
        self.notes: list = []
        self.completed_cycles = 0           # entry+exit замкнулись
        self.force_exit_count = 0
        self.fees_total = Decimal('0')
        self.result = {}

    # ---------- капитал ----------
    def _seed(self, mid: Decimal) -> None:
        if self.seed_mid is not None:
            return
        self.seed_mid = mid
        self.base = (self.cash / mid).quantize(D6, ROUND_HALF_UP)
        self.cash = self.capital / 2

    def _equity(self, book) -> Decimal:
        mid = book.mid or self.seed_mid
        if mid is None:
            return self.cash
        return self.cash + self.base * mid

    def _buy(self, qty: Decimal, price: Decimal, book) -> Decimal:
        notional = (qty * price).quantize(D6, ROUND_HALF_UP)
        fee = (notional * FEE_RATE).quantize(D6, ROUND_HALF_UP)
        if self.cash < notional + fee:      # без займа: лимит капитала
            qty = (self.cash / (price * (1 + FEE_RATE))).quantize(D6, ROUND_HALF_UP)
            if qty <= 0:
                return Decimal('0')
            notional = (qty * price).quantize(D6, ROUND_HALF_UP)
            fee = (notional * FEE_RATE).quantize(D6, ROUND_HALF_UP)
        self.cash -= notional + fee
        self.base += qty
        return qty

    def _sell(self, qty: Decimal, price: Decimal, book) -> Decimal:
        qty = min(qty, self.base)           # не продавать больше, чем есть
        if qty <= 0:
            return Decimal('0')
        notional = (qty * price).quantize(D6, ROUND_HALF_UP)
        fee = (notional * FEE_RATE).quantize(D6, ROUND_HALF_UP)
        self.cash += notional - fee
        self.base -= qty
        return qty

    # ---------- цены ----------
    def _quote_price(self, book, side: str) -> Decimal | None:
        if side == 'buy':
            lv = book.best_bid
            if lv is None:
                return None
            if self.improve:
                better = lv.price + self.tick
                if book.best_ask is None or better < book.best_ask.price:
                    return better
            return lv.price
        lv = book.best_ask
        if lv is None:
            return None
        if self.improve:
            better = lv.price - self.tick
            if book.best_bid is None or better > book.best_bid.price:
                return better
        return lv.price

    def _visible_qty(self, book, side: str, price: Decimal) -> Decimal:
        levels = book.bids if side == 'buy' else book.asks
        for lv in levels:
            if lv.price == price:
                return lv.qty
        return Decimal('0')

    def _place_order(self, book, side: str, ts: float) -> bool:
        price = self._quote_price(book, side)
        if price is None:
            return False
        qty = (self.budget / price).quantize(D6, ROUND_HALF_UP)
        if side == 'buy':
            # проверка капитала без займа
            if self.cash < qty * price * (1 + FEE_RATE):
                qty = ((self.cash) / (price * (1 + FEE_RATE))
                       ).quantize(D6, ROUND_HALF_UP)
                if qty <= 0:
                    self.notes.append('capital_limit')
                    return False
        else:
            if self.base <= 0:
                self.notes.append('no_base_for_exit')
                return False
            qty = min(qty, self.base)
        queue = (self._visible_qty(book, side, price) * self.qf
                 ).quantize(D6, ROUND_HALF_UP)
        self.order = {'side': side, 'price': price, 'qty': qty,
                      'queue_ahead': queue, 'remaining': qty,
                      'placed_ts': ts}
        self.order_active_ts = ts + self.delay_s
        return True

    def _place_exit(self, book, ts: float, qty: Decimal | None = None) -> None:
        # после входа: maker-exit противоположной стороной
        if qty is None:
            if self.order is None:
                return
            qty = self.order['qty']
        price = self._quote_price(book, 'sell')
        if price is None:
            return
        qty = min(qty, self.base).quantize(D6, ROUND_HALF_UP)
        if qty <= 0:
            return
        queue = (self._visible_qty(book, 'sell', price) * self.qf
                 ).quantize(D6, ROUND_HALF_UP)
        self.exit_order = {'side': 'sell', 'price': price, 'qty': qty,
                           'queue_ahead': queue, 'remaining': qty,
                           'placed_ts': ts}
        self.exit_active_ts = ts + self.delay_s

    # ---------- события ----------
    def _refresh_orders(self, book: Book) -> None:
        """Cancel-replace: переставить неисполненные заявки на актуальный BBO.

        Частично исполненные (remaining < qty) остаются на своей цене
        (остаток переносится, как в ТЗ). Полные заявки переставляются на
        текущий лучший уровень с пересчётом очереди впереди — решение
        использует только данные стакана, известные к этому моменту.
        """
        for attr in ('order', 'exit_order'):
            o = getattr(self, attr)
            if o is None or o['remaining'] != o['qty']:
                continue
            if o['remaining'] <= 0:
                continue
            price = self._quote_price(book, o['side'])
            if price is None:
                continue
            o['price'] = price
            o['queue_ahead'] = (self._visible_qty(book, o['side'], price) * self.qf
                                ).quantize(D6, ROUND_HALF_UP)
            o['placed_ts'] = book.ts

    def on_book(self, book: Book) -> None:
        if self.begin_ts is None:
            self.begin_ts = book.ts
            self._seed(book.mid)
        if book.mid is not None:
            self.last_mid = book.mid
        if self.seed_mid is None:
            return
        if self.baseline:
            self.equity.append((book.ts, self._equity(book)))
            return                         # no-trade control: без заявок
        self._refresh_orders(book)
        # удержание: force taker-exit, если позиция открыта дольше 30 минут
        # (отсчёт от момента входа, не от перестановки заявки)
        if self.exit_order is not None and self.entry_ts is not None \
                and book.ts - self.entry_ts > MAX_HOLD_S:
            self._force_exit(book, 'hold_timeout')
        # новая entry, если нет ни entry, ни exit
        if self.order is None and self.exit_order is None:
            self.entry_ts = None
            self._place_order(book, 'buy', book.ts)
        self.equity.append((book.ts, self._equity(book)))

    def _matching_side(self, trade_side: str, our_side: str) -> bool:
        """Заполняет ли сделка нашу заявку (H1: side=taker; H2: инверсия)."""
        if self.h == 'H1':
            return trade_side == ('sell' if our_side == 'buy' else 'buy')
        return trade_side == ('buy' if our_side == 'buy' else 'sell')

    def _consume(self, o: dict, book: Book, ts: float, trade_side: str,
                 amount: Decimal, price: Decimal, reason: str) -> None:
        if o is None:
            return
        if ts < (getattr(self, 'order_active_ts', 0.0) if o is self.order
                 else getattr(self, 'exit_active_ts', 0.0)):
            return                      # задержка активации ещё не прошла
        if not self._matching_side(trade_side, o['side']):
            return
        # Заявка стоит ровно на лучшем уровне (или улучшенном). Исполняет
        # только маркет-трейд, ударивший ровно в НАШУ цену: buy на bid —
        # sell-трейд по нашей цене; sell на ask — buy-трейд по нашей цене.
        # Трейды мимо нашей цены (внутри спреда/через ask) нас не исполняют.
        if price != o['price']:
            return
        # FIFO: гасим очередь впереди
        queued = min(amount, o['queue_ahead'])
        o['queue_ahead'] -= queued
        avail = amount - queued
        if avail <= 0:
            return
        fill = min(avail, o['remaining'])
        if fill <= 0:
            return
        if o['side'] == 'buy':
            executed = self._buy(fill, o['price'], book)
        else:
            executed = self._sell(fill, o['price'], book)
        if executed <= 0:
            return
        if executed < fill:
            # капитал усекся: остаток заявки уменьшается на исполненное
            o['remaining'] = fill - executed if o['remaining'] > fill else o['remaining'] - executed
        else:
            o['remaining'] -= executed
        partial = o['remaining'] > 0
        fee = (executed * o['price'] * FEE_RATE).quantize(D6, ROUND_HALF_UP)
        self.fees_total += fee
        self.fills.append(FillRec(ts=ts, side=o['side'], price=o['price'],
                                  qty=executed, fee=fee, partial=partial,
                                  reason=reason))
        if o['remaining'] <= 0:
            if o is self.order:
                entry_qty = o['qty']
                self.order = None
                if self.entry_ts is None:
                    self.entry_ts = ts
                self._place_exit(book, ts, qty=entry_qty)
            else:
                self.exit_order = None
                if self.order is None:
                    self.completed_cycles += 1   # entry+exit замкнулись
                    self.entry_ts = None

    def on_trade(self, ts: float, price: Decimal, amount: Decimal,
                 trade_side: str, book: Book | None) -> None:
        if book is None:
            return
        # Запрет look-ahead: стакан старше STALE_S не используется.
        if ts - book.ts > STALE_S:
            self.skipped_stale += 1
            return
        self._consume(self.order, book, ts, trade_side, amount, price, 'entry')
        self._consume(self.exit_order, book, ts, trade_side, amount, price, 'exit')

    def _force_exit(self, book: Book, reason: str) -> None:
        """Taker-закрытие остатка: sell по лучшему bid (или buy-остатка нет)."""
        src = self.exit_order if self.exit_order is not None else self.order
        if src is None:
            return
        lv = book.best_bid
        if lv is None or lv.qty <= 0:
            self.notes.append('no_liquidity_force_exit')
            return
        qty = min(src['remaining'], lv.qty, self.base)
        if qty <= 0:
            src['remaining'] = 0
            self.order = self.exit_order = None
            return
        executed = self._sell(qty, lv.price, book)
        if executed <= 0:
            return
        src['remaining'] -= executed
        fee = (executed * lv.price * FEE_RATE).quantize(D6, ROUND_HALF_UP)
        self.fees_total += fee
        self.force_exit_count += 1
        self.fills.append(FillRec(ts=book.ts, side='sell', price=lv.price,
                                  qty=executed, fee=fee, partial=src['remaining'] > 0,
                                  taker=True, reason=reason))
        if src['remaining'] <= 0:
            if self.exit_order is not None:
                self.exit_order = None
                if self.order is None:
                    self.completed_cycles += 1
                    self.entry_ts = None
            self.order = None

    def finalize(self) -> dict:
        mid = self.last_mid or self.seed_mid
        end_equity = self.cash + self.base * (mid or Decimal('1'))
        pnl = (end_equity - self.capital).quantize(D4, ROUND_HALF_UP)
        n = len(self.fills)
        npart = sum(1 for f in self.fills if f.partial)
        turnover = sum(f.qty * f.price for f in self.fills) if self.fills else Decimal('0')
        max_pos = self.base
        max_dd = Decimal('0')
        if self.equity:
            peak = self.equity[0][1]
            for _, v in self.equity:
                peak = max(peak, v)
                if peak > 0:
                    max_dd = max(max_dd, (peak - v) / peak)
        # markout: последний entry-fill -> equity на 5/30/60с после
        markouts = {'5': None, '30': None, '60': None}
        entry = next((f for f in reversed(self.fills) if f.side == 'buy'), None)
        if entry and self.equity:
            entry_eq = None
            for ts, v in self.equity:
                if ts >= entry.ts:
                    entry_eq = v
                    break
            for h in markouts:
                hv = [(ts, v) for ts, v in self.equity if ts >= entry.ts + float(h)]
                if hv and entry_eq is not None:
                    markouts[h] = float((hv[0][1] - entry_eq).quantize(D4, ROUND_HALF_UP))
        self.result = {
            'pair': self.pair, 'hypothesis': self.h, 'queue_factor': str(self.qf),
            'delay_s': self.delay_s, 'budget_usdt': float(self.budget),
            'improve': self.improve, 'tick': float(self.tick),
            'baseline': self.baseline,
            'final_nav': float(end_equity.quantize(D4, ROUND_HALF_UP)),
            'net_pnl': float(pnl), 'return_on_100_pct': float(
                (pnl / self.capital * 100).quantize(Decimal('0.001'), ROUND_HALF_UP)),
            'n_fills': n, 'n_partial': npart,
            'completed_cycles': self.completed_cycles,
            'force_exit_count': self.force_exit_count,
            'fees_total': float(self.fees_total.quantize(D4, ROUND_HALF_UP)),
            'partial_share': float((Decimal(npart) / Decimal(max(1, n)))
                                   .quantize(Decimal('0.0001'), ROUND_HALF_UP)),
            'turnover_usdt': float(turnover.quantize(D4, ROUND_HALF_UP)),
            'max_position_base': float(max_pos.quantize(D6, ROUND_HALF_UP)),
            'max_drawdown': float(max_dd.quantize(D4, ROUND_HALF_UP)),
            'holding_time_s': [round(f.ts - self.begin_ts, 2)
                               for f in self.fills if self.begin_ts],
            'markout_5s': markouts['5'], 'markout_30s': markouts['30'],
            'markout_60s': markouts['60'],
            'unrealized_qty': float((self.base if self.order or self.exit_order
                                     else Decimal('0')).quantize(D6, ROUND_HALF_UP)),
            'skipped_stale': self.skipped_stale, 'notes': self.notes,
        }
        return self.result


# ---------------------------------------------------------------------------
def load_events(pair_upper: str, run_dir: str, window_end_ts: float):
    """Читает depth-снимки и трейды пары в окне (от window_start в вызывающем)."""
    from pathlib import Path
    base = Path(run_dir)
    books, trades = [], []
    with open(base / 'mk_18db19212772b000_depth.jsonl') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if (r.get('pair') or '').upper() != pair_upper:
                continue
            b = parse_book(r)
            if b is None:
                continue
            books.append(b)
    with open(base / 'mk_18db19212772b000_trades.jsonl') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            poll = json.loads(line)
            if (poll.get('pair') or '').upper() != pair_upper:
                continue
            for t in poll.get('trades', []):
                ts = _parse_iso(t.get('created_at'))
                if ts is None:
                    continue
                trades.append((ts, _d(t['price']), _d(t['amount']),
                               t.get('side')))
    trades.sort(key=lambda x: x[0])
    books = [b for b in books if b.ts <= window_end_ts]
    trades = [t for t in trades if t[0] <= window_end_ts]
    return books, trades
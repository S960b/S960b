"""Offline long-only paper model. Each (hypothesis, delay, budget) has its own account.

No exchange orders are sent. Entries model a capped, complete fill: decision filters
use only the decision-time book; arrival-time prices only determine fill/rejection.
REST-only books are observational and are excluded from execution.
"""
import math
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

import numpy as np

from analysis.oracle import Oracle, asof_value, oracle_asof, oracle_columns, F_from_premium
from analysis.book import OrderBook, payload_dict


@dataclass
class Signal:
    t0_ns: int
    R0: float
    F0: float
    direction: str
    n_confirm: int
    per_source: list
    b: float
    W_s: int
    threshold: float = 0.0
    signal_utc_ns: int = None


@dataclass
class Trade:
    symbol: str
    hypo: str
    W_s: int
    D_s: int
    H_s: int
    L_ms: int
    size_usdt: float
    signal_utc_ns: int
    entry_t_ns: int
    exit_t_ns: int
    entry_vwap: Decimal
    exit_vwap: Decimal
    qty: Decimal
    fee_buy: Decimal
    fee_sell: Decimal
    pnl: Decimal
    exit_reason: str
    adverse_bps: float = None
    reach_raw: bool = False
    reach_adj: bool = False
    status: str = 'closed'
    cash_after: Decimal = None
    equity_after: Decimal = None


class OrderBookReplay:
    """Indexed snapshots; reset barriers and quality flags survive replay."""
    def __init__(self, events_by_mono, max_age_s=5.0):
        self.events = sorted(events_by_mono, key=lambda e: e['recv_monotonic_ns'])
        self._times = [int(e['recv_monotonic_ns']) for e in self.events]
        self.max_age_s = max_age_s
        self.record_end_ns = self._times[-1] if self._times else 0
        self._starts = []
        start = 0
        for i, e in enumerate(self.events):
            if e['event_type'] == 'book_snapshot' or 'connection_reset' in (e.get('quality_flags') or []):
                start = i
            self._starts.append(start)

    def asof_ns(self, t_ns, max_age_s=None, require_executable=True):
        age = self.max_age_s if max_age_s is None else max_age_s
        idx = bisect_right(self._times, int(t_ns)) - 1
        if idx < 0:
            return None
        ob = OrderBook()
        for e in self.events[self._starts[idx]:idx+1]:
            if e['event_type'] == 'book_delta' and e['exchange'] == 'safetrade' and not payload_dict(e).get('synchronized'):
                continue
            try:
                ob.apply(e)
            except (ValueError, ArithmeticError):
                ob.valid = ob.executable = False
        if not ob.valid or ob.mid() is None or (t_ns-ob.last_update_mono_ns)/1e9 > age:
            return None
        if require_executable and not ob.executable:
            return None
        return ob


class PaperSimulator:
    def __init__(self, cfg, symbols):
        self.cfg = cfg
        self.paper_cfg = cfg['paper']
        self.fees = cfg.get('fees', {}).get('safetrade', {}).get('taker_bps', 0.0) / 1e4
        self.sources = cfg['sources']
        self.symbols = symbols
        self._premium_t = self._premium_b = None
        self._ext = None
        self._r_columns = None

    def _build_r_series(self, ext):
        rows = [(int(t), ex, float(m)) for ex, s in ext.items()
                for t, m in zip(s.mono_ns, s.mid)]
        rows.sort(key=lambda x: x[0])
        oracle = Oracle(self.sources, self.cfg['fitness']['max_bbo_age_s'], 3)
        out = []
        for t, ex, mid in rows:
            oracle.feed(ex, t, mid)
            r, n, _ = oracle.mid_at(t)
            expires = min(oracle._last[s][0]+int(self.cfg['fitness']['max_bbo_age_s']*1e9) for s in self.sources) if r is not None else t
            out.append((t, r if r is not None else float('nan'), n, expires))
        return np.array(out, dtype=object) if out else None

    def _source_mid(self, s, t):
        return asof_value(s.mono_ns.to_numpy(dtype=np.int64), s.mid.to_numpy(dtype=float),
                          int(t), self.cfg['fitness']['max_bbo_age_s'])[1]

    def _return(self, s, t, W_ns):
        a, b = self._source_mid(s, t-W_ns), self._source_mid(s, t)
        if a is None or b is None:
            return None
        times = s.mono_ns.to_numpy(dtype=np.int64)
        lo = max(0, bisect_right(times, int(t-W_ns))-1)
        hi = bisect_right(times, int(t))
        values = s.mid.to_numpy(dtype=float)[lo:hi]
        if not np.isfinite(values).all() or (hi-lo > 1 and np.diff(times[lo:hi]).max()/1e9 > self.cfg['fitness']['max_bbo_age_s']):
            return None
        return math.log(float(b)/float(a))

    def detect_signals(self, mid_series_by_ex, t0_ns, t1_ns, W_s, D_s):
        funnel = Counter({k: 0 for k in ('sources_stale', 'external_history_missing', 'weak_move',
                                         'disagree', 'oracle_dispersion', 'cooldown_skipped', 'signal')})
        ext = {k: v for k, v in mid_series_by_ex.items() if k in self.sources}
        self._ext = ext
        if len(ext) != 3:
            return [], dict(funnel)
        rs = self._build_r_series(ext)
        if rs is None:
            return [], dict(funnel)
        self._r_columns = oracle_columns(rs)
        tr, rv, _ = self._r_columns
        signals, cooldown = [], 0
        last_t = None
        for j, t in enumerate(tr):
            if t < t0_ns or t > t1_ns or (j+1 < len(tr) and tr[j+1] == t):
                continue
            if t < cooldown:
                funnel['cooldown_skipped'] += 1
                continue
            last_t = t
            values = [self._source_mid(s, t) for s in ext.values()]
            if any(v is None for v in values) or not math.isfinite(rv[j]):
                funnel['sources_stale'] += 1; continue
            before = oracle_asof(rs, int(t-W_s*1e9), self.cfg['fitness']['max_bbo_age_s'], self._r_columns)
            if before is None:
                funnel['external_history_missing'] += 1; continue
            d = math.log(rv[j]/float(before))
            thr = self._past_noise_threshold(rs, 0, j, W_s)
            if abs(d) < thr:
                funnel['weak_move'] += 1; continue
            direction = 'up' if d > 0 else 'down'
            agrees, opposes, per = self._confirm(ext, t, W_s*1e9, direction, thr)
            if agrees < self.paper_cfg['confirm_min_sources'] or opposes:
                funnel['external_history_missing' if any(x[1]=='no_data' for x in per) else 'disagree'] += 1; continue
            if not self._spread_ok(ext, t):
                funnel['oracle_dispersion'] += 1; continue
            b, _ = self._premium_asof(t)
            # UTC comes from the timestamp of a received event, never from monotonic.
            utc = None
            for s in ext.values():
                k = bisect_right(s.mono_ns.to_numpy(dtype=np.int64), int(t))-1
                if k >= 0 and 'utc_ns' in s:
                    utc = int(s.utc_ns.iloc[k]) + int(t) - int(s.mono_ns.iloc[k])
                    break
            signals.append(Signal(int(t), float(rv[j]), F_from_premium(float(rv[j]), b),
                                  direction, agrees, per, b, W_s, thr, utc))
            funnel['signal'] += 1
            cooldown = int(t + self.paper_cfg['cooldown_s']*1e9)
        return signals, dict(funnel)

    def _past_noise_threshold(self, r_series, i, j, W_s):
        # Compare W-second returns to past W-second returns, on a fixed cadence.
        # Per-message differences would make the threshold depend on traffic volume.
        columns = self._r_columns if self._r_columns is not None else oracle_columns(r_series)
        t, r, _ = columns
        end = int(t[j])
        age = self.cfg['fitness']['max_bbo_age_s']
        returns = []
        for u in range(int(end-max(20*W_s, 60)*1e9), end, max(1, int(W_s*1e9))):
            a = oracle_asof(r_series, u-int(W_s*1e9), age, columns)
            b = oracle_asof(r_series, u, age, columns)
            if a is not None and b is not None:
                returns.append(math.log(float(b)/float(a)))
        noise = self.paper_cfg['noise']
        mad = float(np.median(np.abs(np.array(returns)-np.median(returns)))) if len(returns)>=10 else 0
        return max(noise['abs_min_bps']/1e4, noise['mad_mult']*mad)

    def _confirm(self, ext, t_ns, W_ns, direction, thr):
        agrees, opposes, per = 0, 0, []
        for ex, s in ext.items():
            d = self._return(s, int(t_ns), int(W_ns))
            if d is None:
                per.append((ex, 'no_data')); continue
            signed = d if direction == 'up' else -d
            if signed >= thr*.5:
                agrees += 1; status = 'agree'
            elif signed < -thr*.25:
                opposes += 1; status = 'oppose'
            else:
                status = 'neutral'
            per.append((ex, status))
        return agrees, opposes, per

    def _spread_ok(self, ext, t_ns, max_spread_bps=None):
        values = [self._source_mid(s, t_ns) for s in ext.values()]
        if len(values) != 3 or any(v is None for v in values):
            return False
        limit = self.paper_cfg.get('oracle_dispersion_bps', 15) if max_spread_bps is None else max_spread_bps
        return (max(values)-min(values))/float(np.median(values))*1e4 <= limit

    def _premium_asof(self, t_ns):
        if self._premium_t is None:
            return None, 0
        age = self.cfg.get('oracle', {}).get('premium_max_age_s', 60)
        k, b = asof_value(self._premium_t, np.exp(self._premium_b), int(t_ns), age)
        # asof_value accepts positive values; exp preserves negative/zero premiums.
        return (math.log(float(b)), k+1) if b is not None else (None, 0)

    def set_premium_series(self, t_arr, b_arr):
        self._premium_t = np.asarray(t_arr, dtype=np.int64)
        self._premium_b = np.asarray(b_arr, dtype=float)

    def run_hypothesis(self, signals, ob_replay, hypo, W_s, D_s, H_s, L_ms_list, sizes_usdt, symbol):
        trades, scenarios = [], {}
        fee = Decimal(str(self.fees))
        buffer = Decimal(str(self.paper_cfg.get('buffer_bps', 5)/1e4))
        for delay in L_ms_list:
            for size in sizes_usdt:
                cash = Decimal(str(self.paper_cfg.get('initial_balance_usdt', 100)))
                busy_until, opened = 0, None
                reasons = Counter()
                closed_pnl = fees_paid = Decimal(0)
                n_entries = n_closed = 0
                n_unknown_entries = 0
                for sig in sorted(signals, key=lambda x: x.t0_ns):
                    decision = int(sig.t0_ns+D_s*1e9)
                    if sig.direction != 'up':
                        reasons['long_only'] += 1; continue
                    if opened is not None or decision < busy_until:
                        reasons['position_busy'] += 1; continue
                    if sig.F0 is None:
                        reasons['premium_unavailable'] += 1; continue
                    if self._ext is not None:
                        # D is a persistence check using only data received by decision.
                        a, o, _ = self._confirm(self._ext, decision, (W_s+D_s)*1e9, 'up', sig.threshold)
                        if a < self.paper_cfg['confirm_min_sources'] or o or not self._spread_ok(self._ext, decision):
                            reasons['persistence_failed'] += 1; continue
                    book = ob_replay.asof_ns(decision)
                    if book is None:
                        reasons['decision_book_unusable'] += 1; continue
                    budget = Decimal(str(size))
                    if budget > cash:
                        reasons['insufficient_cash'] += 1; continue
                    # Predetermined price cap keeps the expected edge positive after fees/buffer.
                    cap = Decimal(str(sig.F0))*(1-fee)/((1+fee)*(1+buffer))
                    qty = budget/(cap*(1+fee))
                    step = self.paper_cfg.get('qty_step')
                    if step:
                        unit = Decimal(str(step)); qty = (qty/unit).to_integral_value(rounding=ROUND_DOWN)*unit
                    price, filled = book.vwap('ask', qty)
                    if price is None or filled != qty or price >= cap:
                        reasons['no_decision_edge_or_depth'] += 1; continue
                    arrival = int(decision+delay*1e6)
                    actual = ob_replay.asof_ns(arrival) if arrival <= ob_replay.record_end_ns else None
                    if actual is None:
                        # The order was already decided. Missing arrival data cannot
                        # retrospectively cancel it or prove that it was not filled.
                        n_unknown_entries += 1
                        opened = {'type': 'entry_unknown', 'requested_qty': str(qty),
                                  'reserved_budget_usdt': str(budget), 'mark_equity_usdt': None}
                        trades.append(Trade(symbol, hypo, W_s, D_s, H_s, delay, size,
                                            sig.signal_utc_ns, arrival, None, None, None, qty,
                                            Decimal(0), Decimal(0), None, 'entry_unobserved',
                                            status='entry_unknown', cash_after=cash, equity_after=None))
                        reasons['entry_unobserved'] += 1
                        continue
                    price, filled = self._limited_vwap(actual, 'ask', qty, cap)
                    if price is None or filled != qty:
                        reasons['entry_unfilled'] += 1; continue
                    cost = qty*price*(1+fee)
                    cash -= cost; n_entries += 1
                    buy_fee = qty*price*fee
                    fees_paid += buy_fee
                    exit_decision = int(arrival+H_s*1e9)
                    exit_arrival = int(exit_decision+delay*1e6)
                    exit_price, exit_qty = None, Decimal(0)
                    if exit_arrival <= ob_replay.record_end_ns:
                        exit_book = ob_replay.asof_ns(exit_arrival)
                        if exit_book is not None:
                            exit_price, exit_qty = exit_book.vwap('bid', qty)
                    closed = exit_price is not None and exit_qty == qty
                    if closed:
                        sell_fee = qty*exit_price*fee
                        proceeds = qty*exit_price*(1-fee)
                        pnl = proceeds-cost
                        cash += proceeds; closed_pnl += pnl; fees_paid += sell_fee
                        n_closed += 1; busy_until = exit_arrival
                        status, reason, equity = 'closed', 'H_timeout', cash
                    else:
                        # Never discard an entry just because its future exit cannot be observed.
                        sell_fee, pnl = Decimal(0), None
                        status, reason = 'open_unknown', 'exit_unobserved_or_insufficient_depth'
                        mark = ob_replay.asof_ns(ob_replay.record_end_ns)
                        mark_price, mark_qty = mark.vwap('bid', qty) if mark else (None, Decimal(0))
                        equity = cash+qty*mark_price*(1-fee) if mark_price is not None and mark_qty==qty else None
                        opened = {'qty': str(qty), 'entry_cost_usdt': str(cost), 'mark_equity_usdt': float(equity) if equity is not None else None}
                    tr = Trade(symbol, hypo, W_s, D_s, H_s, delay, size, sig.signal_utc_ns,
                               arrival, exit_arrival if closed else None, price, exit_price if closed else None,
                               qty, buy_fee, sell_fee, pnl, reason, status=status, cash_after=cash, equity_after=equity,
                               reach_raw=closed and float(exit_price)>=sig.R0,
                               reach_adj=closed and float(exit_price)>=sig.F0)
                    trades.append(tr)
                key = f'L{delay}_Q{size}'
                scenarios[key] = {'L_ms': delay, 'budget_usdt': size, 'n_entries': n_entries,
                                  'n_closed': n_closed, 'n_open_unknown': int(opened is not None),
                                  'n_unknown_entries': n_unknown_entries,
                                  'cash_usdt': float(cash), 'realized_pnl_usdt': float(closed_pnl),
                                  'available_cash_usdt': float(cash-Decimal(opened.get('reserved_budget_usdt', '0'))) if opened else float(cash),
                                  'fees_paid_usdt': float(fees_paid), 'open_position': opened,
                                  'equity_usdt': opened['mark_equity_usdt'] if opened else float(cash),
                                  'rejections': dict(reasons)}
        return {'hypo': hypo, 'stats': {'n_signals': len(signals), 'scenarios': scenarios}, 'trades': trades}

    @staticmethod
    def _limited_vwap(book, side, qty, cap):
        if book is None:
            return None, Decimal(0)
        remaining, total, filled = qty, Decimal(0), Decimal(0)
        for price, volume in book.top_levels(side, 100):
            if side == 'ask' and price > cap:
                break
            take = min(volume, remaining)
            total += take*price; filled += take; remaining -= take
            if remaining <= 0:
                break
        return (total/filled if filled else None), filled

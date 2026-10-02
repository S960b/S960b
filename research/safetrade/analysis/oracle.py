"""Causal receive-time oracle, isolated books and premium from paired observations."""
import math
import statistics
from bisect import bisect_right
from collections import deque

import numpy as np

from .book import payload_dict


def asof_value(times_np, values_np, t_ns, max_age_s=None):
    idx = bisect_right(times_np, int(t_ns)) - 1
    if idx < 0 or (max_age_s is not None and (t_ns - times_np[idx]) / 1e9 > max_age_s):
        return -1, None
    value = values_np[idx]
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        return idx, None
    return idx, value


def oracle_columns(series):
    return (series[:, 0].astype(np.int64), series[:, 1].astype(float),
            series[:, 3].astype(np.int64) if series.shape[1] > 3 else None)


def oracle_asof(series, t_ns, max_age_s=5.0, columns=None):
    if series is None:
        return None
    times, values, expiry = columns if columns is not None else oracle_columns(series)
    idx, value = asof_value(times, values, t_ns, max_age_s)
    if value is None or (expiry is not None and t_ns > expiry[idx]):
        return None
    return float(value)


class Oracle:
    def __init__(self, sources, max_bbo_age_s=5.0, min_sources=3):
        self.sources = list(sources)
        if len(set(sources)) != len(sources):
            raise ValueError('Duplicate oracle source')
        self.max_bbo_age_s = max_bbo_age_s
        self.min_sources = min_sources
        self.reset()

    def feed(self, exchange, t_ns, mid):
        if exchange not in self._last:
            return
        old_t, _ = self._last[exchange]
        if old_t is not None and t_ns < old_t:
            raise ValueError('Oracle events must be time ordered')
        self._last[exchange] = (int(t_ns), mid)

    def mid_at(self, t_ns):
        vals, ids = [], []
        for s in self.sources:
            t, m = self._last[s]
            if t is None or t > t_ns or m is None or not math.isfinite(float(m)) or m <= 0:
                continue
            if (t_ns - t) / 1e9 <= self.max_bbo_age_s:
                vals.append(float(m)); ids.append(s)
        return (float(statistics.median(vals)) if len(vals) >= self.min_sources else None), len(vals), ids

    def reset(self):
        self._last = {s: (None, None) for s in self.sources}


def build_oracle_series(events_df, sources, max_bbo_age_s=5.0, min_sources=3):
    oracle = Oracle(sources, max_bbo_age_s, min_sources)
    rows = []
    for ev in events_df.sort_values('recv_monotonic_ns', kind='stable').to_dict('records'):
        if ev['exchange'] not in sources or not ev.get('_selected', True):
            continue
        t = int(ev['recv_monotonic_ns'])
        oracle.feed(ev['exchange'], t, ev.get('_mid'))
        r, n, _ = oracle.mid_at(t)
        # Keep invalid barriers. Dropping them would reuse the last healthy oracle.
        expires = min(oracle._last[s][0]+int(max_bbo_age_s*1e9) for s in sources) if r is not None else t
        rows.append((t, r if r is not None else float('nan'), n, expires))
    # Object dtype preserves integer nanoseconds instead of silently rounding to float.
    return np.array(rows, dtype=object) if rows else None


def premium_series(safe_mids, r_series, window_s=1800.0, min_points=10,
                   max_r_age_s=5.0, min_span_s=0.0):
    """At t: median of past log(S(u)/R(u)), u<t, within rolling window.

    Every SafeTrade observation is paired with the oracle available at THAT observation.
    The current observation is added only AFTER b(t) is computed. No synthetic prices.
    min_span_s is a warm-up requirement; no event-rate based confidence claim is made.
    """
    out_t, out_b, out_cov = [], [], []
    if r_series is None:
        return np.array([], dtype=np.int64), np.array([]), np.array([], dtype=int)
    columns = oracle_columns(r_series)
    pairs = deque()
    for t, m in sorted(safe_mids, key=lambda x: x[0]):
        t = int(t)
        while pairs and pairs[0][0] < t - window_s * 1e9:
            pairs.popleft()
        if len(pairs) >= min_points and (pairs[-1][0] - pairs[0][0]) / 1e9 >= min_span_s:
            out_t.append(t)
            out_b.append(float(statistics.median(p[1] for p in pairs)))
            out_cov.append(len(pairs))
        r = oracle_asof(r_series, t, max_r_age_s, columns)
        if r is not None and m is not None and math.isfinite(float(m)) and m > 0:
            # One pair per timestamp, not one per concurrent channel message.
            if pairs and pairs[-1][0] == t:
                pairs.pop()
            pairs.append((t, math.log(float(m) / float(r))))
    return np.array(out_t, dtype=np.int64), np.array(out_b), np.array(out_cov, dtype=int)


def F_from_premium(R0, b):
    return None if b is None or not math.isfinite(float(b)) else R0 * math.exp(b)


def stream_key(ev):
    return tuple(ev.get(k, '') for k in ('run_id', 'boot_id', 'exchange', 'canonical_symbol'))


def prepare_mid_column(df):
    """Use dedicated BBO where available; never merge exchange/symbol/session books.

    For SafeTrade only independent REST snapshots are primary observations. Its WS
    deltas stay in storage for protocol research and cannot modify REST observations.
    Invalid selected events remain in the series as barriers.
    """
    from .book import OrderBook, _bbo_mid_from_payload
    if df.empty:
        df['_mid'] = np.nan; df['_selected'] = False
        return df
    df.sort_values('recv_monotonic_ns', kind='stable', inplace=True)
    records = df.to_dict('records')
    bbo_keys = {stream_key(e) for e in records if e['event_type'] == 'bbo'}
    books, mids, selected = {}, [], []
    for ev in records:
        key, et = stream_key(ev), ev['event_type']
        flags = ev.get('quality_flags') or []
        if isinstance(flags, str):
            import json
            flags = json.loads(flags)
        is_reset = 'connection_reset' in flags
        # SafeTrade WS liveness does not invalidate an independent REST observation.
        if is_reset and ev.get('exchange') == 'safetrade' and payload_dict(ev).get('channel') == 'ws':
            mids.append(None); selected.append(False)
            continue
        m, used = None, False
        if is_reset:
            books.pop(key, None)
            used = True
        elif et == 'bbo' and key in bbo_keys:
            used = True
            if not set(flags) & {'book_invalid', 'no_snapshot_yet'}:
                m = _bbo_mid_from_payload(ev)
        elif key not in bbo_keys and et in ('book_snapshot', 'book_delta'):
            unsynced = ev.get('exchange') == 'safetrade' and et == 'book_delta' and not payload_dict(ev).get('synchronized')
            if not unsynced:
                used = True
                ob = books.setdefault(key, OrderBook())
                try:
                    ob.apply(ev)
                    v = ob.mid()
                    m = float(v) if v is not None else None
                except (ValueError, ArithmeticError):
                    ob.valid = ob.executable = False
        mids.append(m); selected.append(used)
    df['_mid'] = mids
    df['_selected'] = selected
    return df

"""Единый causal-оракул (ревью P1-6/P1-9): R(t)=median(mid_i) ТОЛЬКО по трём внешним,
последнее пригодное состояние каждого источника на момент t (backward-asof, без будущего).
Один модуль R/b/F для live/replay/analyze/paper/UI. b(t)=median(log(mid_safe/R)) по прошлым
проверенным данным; warm-up -> запрет входа; F0 блокируется при непроверенной b."""
import math
import statistics
from bisect import bisect_right

import numpy as np


def asof_value(times_np, values_np, t_ns, max_age_s=None):
    """Последний пригодный backward-asof на момент t (без будущего). Возвращает (idx, value)."""
    idx = bisect_right(times_np, t_ns) - 1
    if idx < 0:
        return -1, None
    if max_age_s is not None and (t_ns - times_np[idx]) / 1e9 > max_age_s:
        return -1, None
    return idx, values_np[idx]


class Oracle:
    """Потоковый merge: на каждом событии любого внешнего источника считаем R по последним
    состояниям всех трёх. Требование 3/3 (по умолчанию); 2/3 — degraded, помечается."""

    def __init__(self, sources: list, max_bbo_age_s: float = 5.0, min_sources: int = 3):
        self.sources = sources
        self.max_bbo_age_s = max_bbo_age_s
        self.min_sources = min_sources
        self._last = {s: (None, None) for s in sources}   # (t_ns, mid)

    def feed(self, exchange: str, t_ns: int, mid: float):
        """Вызывается на каждом полученном/воспроизведённом BBO-событии внешнего источника."""
        self._last[exchange] = (t_ns, mid)

    def mid_at(self, t_ns: int):
        """R(t): медиана последних пригодных mid трёх источников на момент t."""
        vals, ids = [], []
        for s in self.sources:
            t, m = self._last[s]
            if t is None or m is None:
                continue
            if (t_ns - t) / 1e9 > self.max_bbo_age_s:
                continue
            vals.append(m)
            ids.append(s)
        if len(vals) < self.min_sources:
            return None, len(vals), ids
        return float(statistics.median(vals)), len(vals), ids

    def reset(self):
        self._last = {s: (None, None) for s in self.sources}


def build_oracle_series(events_df, sources: list, max_bbo_age_s: float = 5.0, min_sources: int = 3):
    """По parquet-событиям (отсортированным по recv_monotonic_ns) строит ряд (t, R, n_src).
    После точки входа ['dev_end'] оракул нельзя кормить будущим — здесь это весь датасет,
    а для live об этом заботится вызывающий. Возвращает np.array Nx3."""
    oracle = Oracle(sources, max_bbo_age_s, min_sources)
    rows = []
    for _, ev in events_df.iterrows():
        if ev["exchange"] not in sources:
            continue
        mid = ev.get("_mid")   # предрасчитанный mid (см. prepare_mid_column)
        if mid is None or mid <= 0:
            continue
        oracle.feed(ev["exchange"], int(ev["recv_monotonic_ns"]), float(mid))
        # R на момент события (as-of: сам источник уже обновлён — это ok, его событие и есть now)
        R, n, ids = oracle.mid_at(int(ev["recv_monotonic_ns"]))
        if R is not None:
            rows.append((int(ev["recv_monotonic_ns"]), R, n))
    if not rows:
        return None
    return np.array(rows)


def premium_series(safe_mids, r_series, window_s: float = 1800.0, min_points: int = 10):
    """b(t)=median(log(mid_safe/R)) по ПРОШЛЫМ данным [t-window, t) (ревью P1-9).
    Возвращает (t_array, b_array, coverage_array) — causal: b(t) известен только на t после
    накопления окна. На вход — серия событий mid SafeTrade и ряда R (as-of)."""
    out_t, out_b, out_cov = [], [], []
    # r_series: Nx3 (t, R, n_src)
    rt = r_series[:, 0]
    rv = r_series[:, 1]
    for i in range(len(safe_mids)):
        t = safe_mids[i][0]
        m = safe_mids[i][1]
        if m <= 0:
            continue
        # окно прошлого: [t-window, t)
        w0 = t - window_s * 1e9
        j0 = bisect_right(rt, w0)
        j1 = bisect_right(rt, t)
        if j1 - j0 < min_points:
            continue   # warm-up: b ещё не определён
        diffs = []
        for j in range(j0, j1):
            R = rv[j]
            if R and R > 0:
                diffs.append(math.log(m / R))
        if len(diffs) < min_points:
            continue
        out_t.append(t)
        out_b.append(float(statistics.median(diffs)))
        out_cov.append(len(diffs))
    return np.array(out_t), np.array(out_b), np.array(out_cov)


def F_from_premium(R0: float, b: float):
    """F0 = R0 * exp(b). b=None -> F0=None (заблокировано, не подменять R0)."""
    if b is None:
        return None
    return R0 * math.exp(b)


def prepare_mid_column(df):
    """Добавляет колонку _mid для bbo (из payload-контракта) и book событий (из стакана).
    Меняет df на месте. Для bbo — единый контракт bid_price/ask_price."""
    from .book import OrderBook, _bbo_mid_from_payload
    mids = []
    ob = OrderBook()
    for _, ev in df.iterrows():
        et = ev["event_type"]
        if et == "bbo":
            m = _bbo_mid_from_payload(ev)
            mids.append(m)
        elif et in ("book_snapshot", "book_delta"):
            ob.apply(ev.to_dict())
            m = ob.mid()
            mids.append(float(m) if m is not None else None)
        else:
            mids.append(None)
    df["_mid"] = mids
    return df
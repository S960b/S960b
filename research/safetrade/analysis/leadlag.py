"""Анализ опережения (ревью P1-8): БЕЗ np.interp — никакого рисования значений в промежутках.
backward-asof в пределах валидных интервалов; returns на событиях; сетка только для статистики
покрытия. Лаг в секундах на общей оси receive-time."""
import math
import statistics
from bisect import bisect_right

import numpy as np


def _asof(times, values, t_ns, max_age_s=None):
    idx = bisect_right(times, t_ns) - 1
    if idx < 0:
        return None
    if not math.isfinite(float(values[idx])) or values[idx] <= 0:
        return None
    if max_age_s is not None and (t_ns - times[idx]) / 1e9 > max_age_s:
        return None
    return float(values[idx])


def shift_stats_on_events(series, W_s: float):
    """Изменения log(mid) за окно W, считанные ПО СОБЫТИЯМ (не по интерполированной сетке).
    Возвращает (t_end_array, delta_array)."""
    mono = series["mono_ns"].values
    vals = series["mid"].values.astype(float)
    out_ts, out_d = [], []
    W_ns = W_s * 1e9
    for j in range(len(mono)):
        i = bisect_right(mono, int(mono[j] - W_ns)) - 1
        if i >= 0 and i < j and mono[j] - W_ns - mono[i] <= W_ns * 0.5 and np.isfinite(vals[i:j+1]).all() and min(vals[i], vals[j]) > 0:
            out_ts.append(int(mono[j]))
            out_d.append(float(math.log(vals[j] / vals[i])))
    return np.array(out_ts, dtype=np.int64), np.array(out_d, dtype=np.float64)


def lead_lag(external_mids: dict, safe_mids, W_s: float, max_lag_s: int = 30, step_s: int = 1,
             max_age_s: float = 5.0):
    """Кросс-корреляция лидер-лаг: для каждого события внешнего источника считаем его движение
    за W, и движение SafeTrade за те же W, смещённое на lag (по receive-time as-of, без будущего).
    lag>0 = внешний опережает. Возвращает {lag_sec: corr} и метаданные покрытия."""
    res = {}
    meta = {}

    for ex, s in external_mids.items():
        if len(s) < 30:
            continue
        t_ext, d_ext = shift_stats_on_events(s, W_s)
        safe_t = safe_mids["mono_ns"].values
        safe_v = safe_mids["mid"].values
        if len(d_ext) < 20:
            meta[ex] = {"n_events": len(d_ext), "note": "too few events"}
            continue
        corrs = {}
        for lag in range(-max_lag_s, max_lag_s + 1, step_s):
            xs, ys, n = [], [], 0
            for k in range(len(t_ext)):
                t0 = t_ext[k]
                # External return ends at t0; target return ends at t0+lag.
                # Positive lag means SafeTrade follows later. This is retrospective
                # measurement, never information available to a live decision at t0.
                t_lag = int(t0 + lag * 1e9)
                v0 = _asof(safe_t, safe_v, int(t_lag - W_s * 1e9), max_age_s)
                vW = _asof(safe_t, safe_v, t_lag, max_age_s)
                lo = bisect_right(safe_t, int(t_lag - W_s * 1e9)) - 1
                hi = bisect_right(safe_t, t_lag)
                if lo < 0 or not np.isfinite(safe_v[lo:hi]).all() or (hi-lo > 1 and np.diff(safe_t[lo:hi]).max() / 1e9 > max_age_s):
                    continue
                if v0 is None or vW is None or v0 <= 0:
                    continue
                ys.append(math.log(vW / v0))
                xs.append(d_ext[k])
                n += 1
            if n >= 20 and np.std(xs) > 0 and np.std(ys) > 0:
                c = float(np.corrcoef(xs, ys)[0, 1]) if len(xs) > 5 else 0.0
                corrs[lag] = {"corr": c, "n": n}
        res[ex] = corrs
        meta[ex] = {"n_external_events": len(d_ext)}
    return {"correlations": res, "meta": meta}


def coverage_stats(mids_by_exchange: dict, grid_step_s: int = 1):
    """Message-bin occupancy only; it does not measure connection liveness."""
    out = {}
    for ex, s in mids_by_exchange.items():
        if len(s) < 2:
            out[ex] = {"coverage": 0.0, "n": len(s)}
            continue
        t = s["mono_ns"].values
        span_s = (t[-1] - t[0]) / 1e9
        if span_s <= 0:
            out[ex] = {"coverage": 0.0, "n": len(s)}
            continue
        intervals = span_s / grid_step_s
        # число сеточных ячеек с хотя бы одним наблюдением
        cells = int(span_s / grid_step_s) + 1
        if cells == 0:
            out[ex] = {"coverage": 1.0 if len(s) else 0.0, "n": len(s)}
            continue
        idx = np.floor((t - t[0]) / (grid_step_s * 1e9)).astype(int)
        uniq = len(np.unique(idx))
        out[ex] = {"message_bin_occupancy": uniq / cells, "coverage": uniq / cells, "n": len(s), "grid_cells": cells}
    return out


def moving_premium_causal(safe_mids, r_series, window_s: float = 1800.0, min_points: int = 10):
    """b(t) ряд: median(log(safe/R)) по прошлому окну (только до t). R — единый оракул."""
    from .oracle import premium_series
    return premium_series(safe_mids, r_series, window_s, min_points)

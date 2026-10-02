"""Анализ: построение стакана из событий (snapshot+delta), BBO-ряд, оракул, премия, тесты достижения.
Backward/as-of join: только прошлые события (ТЗ п.6)."""
import bisect
import json
import math
import os
import statistics
from decimal import Decimal
from datetime import datetime, timezone

import duckdb
import pandas as pd

from .parquet_index import latest_run_from_index as _latest_run_from_index


def _files_for_latest_run(files: list, max_runs: int = 1):
    """Выбор файлов последнего(их) run по времени записи в имени файла:
    <exchange>_<run_id>_<ns>.parquet — run с наибольшим max(ns) = самый поздний сбор.
    (Сортировка по строке run_id не отражает хронологию: hex-префиксы не монотонны.)"""
    from collections import defaultdict
    runs = defaultdict(list)
    max_ns = {}
    for f in files:
        base = os.path.basename(f)[:-8]  # отрезаем .parquet
        parts = base.split("_")
        rid = "_".join(parts[1:-1]) if len(parts) >= 3 else "unknown"
        runs[rid].append(f)
        try:
            ns = int(parts[-1])
        except (ValueError, IndexError):
            ns = 0
        max_ns[rid] = max(max_ns.get(rid, 0), ns)
    top = sorted(max_ns.items(), key=lambda kv: kv[1])[-max_runs:]
    return [f for rid, _ in top for f in runs[rid]]


def load_parquet_all(parquet_dir: str, exchange=None, symbol=None, event_types=None,
                     run_id=None, max_runs=None, t_min_ns=None, t_max_ns=None,
                     index_path=None) -> pd.DataFrame:
    """Загрузить закрытые parquet-файлы (replay, ТЗ п.5/10).
    Ревью: выбор run/session — по умолчанию ТОЛЬКО последний run (monotonic-часы разных
    загрузок несравнимы). Если задан index_path (reports/parquet_index.json), последний
    run выбирается ПО ДАННЫМ (start_utc_ns), а не по времени записи имени файла (ревью P1.4).
    Чтение через pyarrow.dataset (duckdb виснет на тысячах мелких файлов + union_by_name).
    t_min_ns/t_max_ns — фильтр времени в СКАНЕРЕ (по recv_utc_ns), до материализации
    в pandas: защита от OOM для длинных сессий (ревью P1)."""
    import pyarrow.dataset as ds
    files = []
    for root, _, fs in os.walk(parquet_dir):
        for f in fs:
            if f.endswith(".parquet") and not f.startswith("."):
                files.append(os.path.join(root, f))
    if not files:
        return pd.DataFrame()
    files = sorted(files)
    if run_id:
        files = [f for f in files if f"_{run_id}_" in os.path.basename(f)]
        if not files:
            return pd.DataFrame()
    elif max_runs not in (None, 1):
        raise ValueError("Analyze runs separately: monotonic clocks from different runs cannot be joined")
    elif max_runs == 1 or max_runs is None:
        # последний run по индексу (данные), иначе по времени записи имени файла
        rid = _latest_run_from_index(index_path) if index_path else None
        if rid:
            files = [f for f in files if f"_{rid}_" in os.path.basename(f)]
        else:
            files = _files_for_latest_run(files, max_runs=1)
        if not files:
            return pd.DataFrame()
    dataset = ds.dataset(files, format="parquet")
    # выбор последней сессии (run_id/boot_id) по данным
    tbl = dataset.to_table(columns=["run_id", "boot_id", "recv_utc_ns"])
    meta = tbl.to_pandas()
    if meta.empty:
        return pd.DataFrame()
    g = meta.groupby(["run_id", "boot_id"], sort=False)["recv_utc_ns"].min().reset_index()
    g = g.sort_values("recv_utc_ns", ascending=False)
    target_run, target_boot = g.iloc[0]["run_id"], g.iloc[0]["boot_id"]
    filt = (ds.field("run_id") == target_run) & (ds.field("boot_id") == target_boot)
    if exchange:
        filt = filt & (ds.field("exchange") == exchange)
    if symbol:
        filt = filt & (ds.field("canonical_symbol") == symbol)
    if event_types:
        filt = filt & ds.field("event_type").isin(list(event_types))
    if t_min_ns is not None:
        filt = filt & (ds.field("recv_utc_ns") >= int(t_min_ns))
    if t_max_ns is not None:
        filt = filt & (ds.field("recv_utc_ns") <= int(t_max_ns))
    tbl = dataset.to_table(filter=filt)
    df = tbl.to_pandas()
    df = parse_bids_asks(df)
    return df.sort_values("recv_monotonic_ns").reset_index(drop=True)


def payload_dict(ev):
    """Ленивое чтение payload: в parquet payload хранится строкой, парсим только при обращении."""
    pl = ev.get("payload")
    if isinstance(pl, str):
        try:
            return json.loads(pl)
        except (json.JSONDecodeError, TypeError):
            return {}
    return pl if isinstance(pl, dict) else {}


def parse_bids_asks(df: pd.DataFrame) -> pd.DataFrame:
    """bids/asks/quality_flags из parquet (JSON-строки) → списки. payload НЕ парсим:
    лениво в точке использования (на 1.88М строк json.loads payload виснет)."""
    if df.empty:
        return df
    for col in ("bids", "asks", "quality_flags"):
        if col not in df.columns:
            continue
        out = []
        for x in df[col]:
            if isinstance(x, str) and x not in ("[]", "", "null"):
                out.append(json.loads(x))
            elif isinstance(x, list):
                out.append(x)
            else:
                out.append([])
        df[col] = out
    return df


class OrderBook:
    """Стакан из событий. Применение: снапшот → replace; дельта → qty=0 удаляет уровень."""
    def __init__(self):
        self.bids = {}   # price -> qty (Decimal)
        self.asks = {}
        self.sequence = None
        self.valid = False
        self.executable = False
        self.last_update_mono_ns = 0

    def apply(self, ev: dict):
        et = ev["event_type"]
        if et not in ("book_snapshot", "book_delta", "health"):
            return
        flags = ev.get("quality_flags") or []
        if isinstance(flags, str):
            flags = json.loads(flags)
        if set(flags) & {"book_invalid", "no_snapshot_yet", "sequence_regression", "connection_reset"}:
            self.valid = self.executable = False
            self.bids.clear()
            self.asks.clear()
            return
        if et == "health":
            return
        self.last_update_mono_ns = int(ev["recv_monotonic_ns"])
        if et == "book_snapshot":
            self.bids = {}
            self.asks = {}
            self.valid = True
            self.executable = not (set(flags) & {"rest_provisional", "rest_snapshot", "observation_only"})
            if ev.get("exchange") == "safetrade" and not payload_dict(ev).get("synchronized"):
                self.executable = False
            self._apply_levels(self.bids, ev.get("bids") or [])
            self._apply_levels(self.asks, ev.get("asks") or [])
        elif et == "book_delta":
            if not self.valid:
                return
            self._apply_levels(self.bids, ev.get("bids") or [], positive_side=True)
            self._apply_levels(self.asks, ev.get("asks") or [], positive_side=False)
        if ev.get("sequence") is not None:
            self.sequence = ev["sequence"]
        if self.bids and self.asks and max(self.bids) > min(self.asks):
            self.valid = self.executable = False

    @staticmethod
    def _apply_levels(side_map, levels, positive_side=True):
        for p, q in levels:
            price = Decimal(str(p))
            qty = Decimal(str(q))
            if not price.is_finite() or not qty.is_finite() or price <= 0 or qty < 0:
                raise ValueError("Invalid book level")
            if qty == 0:
                side_map.pop(price, None)
            else:
                side_map[price] = qty

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def mid(self):
        if not self.valid:
            return None
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        return (b + a) / 2

    def top_levels(self, side: str, n: int):
        """Список (price, qty) сверху глубины."""
        m = self.bids if side == "bid" else self.asks
        if not m:
            return []
        ordered = sorted(m.items(), reverse=(side == "bid"))[:n]
        return [(p, q) for p, q in ordered if q > 0]

    def vwap(self, side: str, qty: Decimal, max_levels: int = 100):
        """VWAP исполнения qty против стакана (taker). Возвращает (price, filled)."""
        if not self.valid or qty <= 0:
            return None, Decimal(0)
        m = self.bids if side == "bid" else self.asks
        if not m:
            return None, Decimal(0)
        ordered = sorted(m.items(), reverse=(side == "bid"))[:max_levels]
        remaining = qty
        cost = Decimal(0)
        filled = Decimal(0)
        for price, q in ordered:
            if q <= 0:
                continue
            take = min(remaining, q)
            cost += take * price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            return None, Decimal(0)
        return cost / filled, filled


def bbo_series(df: pd.DataFrame) -> pd.DataFrame:
    """BBO-ряд из событий: mid для каждого события (bbo или изменение стакана)."""
    from .oracle import prepare_mid_column
    frame = prepare_mid_column(df.copy())
    frame = frame[frame._selected]
    return pd.DataFrame({"mono_ns": frame.recv_monotonic_ns.to_numpy(dtype="int64"),
                         "utc_ns": frame.recv_utc_ns.to_numpy(dtype="int64"),
                         "mid": frame._mid.to_numpy(dtype=float)}).sort_values("mono_ns").reset_index(drop=True)


def _bbo_mid_from_payload(ev) -> float:
    """Извлечь mid из payload BBO-события — единый контракт bid_price/ask_price (ревью P0-4)."""
    pl = payload_dict(ev)
    if not isinstance(pl, dict):
        return None
    bid_px = pl.get("bid_price")
    ask_px = pl.get("ask_price")
    # fallback: старые форматы (binance b/a строки, okx bidPx/askPx, bybit [price,qty])
    if bid_px is None or ask_px is None:
        if "b" in pl and "a" in pl and isinstance(pl["b"], str):
            bid_px, ask_px = pl["b"], pl["a"]
        elif "bidPx" in pl and "askPx" in pl:
            bid_px, ask_px = pl["bidPx"], pl["askPx"]
        elif "b" in pl and "a" in pl and isinstance(pl["b"], list):
            if pl["b"] and pl["a"]:
                bid_px, ask_px = pl["b"][0], pl["a"][0]
    try:
        bid = float(bid_px)
        ask = float(ask_px)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0 or bid > ask:
        return None
    return (bid + ask) / 2


def median_of_mids(mids_by_exchange: list, t_ns: int, max_age_s: float = 5.0):
    """Медиана mid по источникам на момент t (as-of). Возвращает (med, per_source, n_valid, ids)."""
    vals = []
    per = {}
    for ex, series in mids_by_exchange:
        idx = bisect.bisect_right(series["mono_ns"].values, t_ns) - 1
        if idx < 0:
            per[ex] = None
            continue
        row = series.iloc[idx]
        age_s = (t_ns - row["mono_ns"]) / 1e9
        if age_s > max_age_s:
            per[ex] = None
            continue
        per[ex] = float(row["mid"])
        vals.append(float(row["mid"]))
    if not vals:
        return None, per, 0, []
    return float(statistics.median(vals)), per, len(vals), [ex for ex, v in per.items() if v is not None]


def compute_premium(safe_mids, ext_mids, t_ns, window_s: int = 1800):
    """b(t) = median(log(mid_safe/R)) за прошлое окно [t-window, t) (ТЗ п.7)."""
    diffs = []
    t0 = t_ns - window_s * 1e9
    safe_idx = bisect.bisect_left(safe_mids["mono_ns"].values, t0)
    if safe_idx >= len(safe_mids):
        return None
    # идём по времени, для каждого события safe смотрим R as-of
    for i in range(safe_idx, len(safe_mids)):
        t = safe_mids.iloc[i]["mono_ns"]
        if t >= t_ns:
            break
        r, _, n, _ = median_of_mids(ext_mids, t)
        if r is None or r <= 0:
            continue
        ms = float(safe_mids.iloc[i]["mid"])
        if ms <= 0:
            continue
        diffs.append(math.log(ms / r))
    if len(diffs) < 10:
        return None
    return statistics.median(diffs), len(diffs)

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


def load_parquet_all(parquet_dir: str, exchange=None, symbol=None, event_types=None,
                     run_id=None, max_runs=None) -> pd.DataFrame:
    """Загрузить закрытые parquet-файлы (replay, ТЗ п.5/10).
    Ревью: выбор run/session — по умолчанию ТОЛЬКО последний run (monotonic-часы разных
    загрузок несравнимы); max_runs — сколько последних брать (для пакетного анализа)."""
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
    elif max_runs is not None:
        # группируем файлы по run_id в имени (exchange_runid_ns.parquet, runid=st_hex)
        from collections import OrderedDict
        runs = OrderedDict()
        for f in files:
            parts = os.path.basename(f)[:-8].split("_")   # отрезаем .parquet
            if len(parts) >= 3:
                rid = "_".join(parts[1:-1])                # run_id = st_<hex>
                runs.setdefault(rid, []).append(f)
        run_keys = list(runs.keys())[-max_runs:]
        files = [f for k in run_keys for f in runs[k]]
    df = duckdb.query(f"""
        SELECT * FROM read_parquet({[f for f in files]!r}, union_by_name=True)
    """).df()
    df = parse_bids_asks(df)
    if exchange:
        df = df[df.exchange == exchange]
    if symbol:
        df = df[df.canonical_symbol == symbol]
    if event_types:
        df = df[df.event_type.isin(event_types)]
    return df.sort_values("recv_monotonic_ns").reset_index(drop=True)


def parse_bids_asks(df: pd.DataFrame) -> pd.DataFrame:
    """bids/asks из parquet хранятся как JSON-строки → списки [[price, qty], ...]."""
    if df.empty:
        return df
    if "bids" in df.columns:
        df["bids"] = df["bids"].apply(lambda x: json.loads(x) if isinstance(x, str) else (x or []))
    if "asks" in df.columns:
        df["asks"] = df["asks"].apply(lambda x: json.loads(x) if isinstance(x, str) else (x or []))
    if "payload" in df.columns:
        df["payload"] = df["payload"].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
    return df


class OrderBook:
    """Стакан из событий. Применение: снапшот → replace; дельта → qty=0 удаляет уровень."""
    def __init__(self):
        self.bids = {}   # price -> qty (Decimal)
        self.asks = {}
        self.sequence = None
        self.valid = False
        self.last_update_mono_ns = 0

    def apply(self, ev: dict):
        self.last_update_mono_ns = ev["recv_monotonic_ns"]
        et = ev["event_type"]
        if et == "book_snapshot":
            self.bids = {}
            self.asks = {}
            for p, q in ev.get("bids") or []:
                self.bids[Decimal(p)] = Decimal(q)
            for p, q in ev.get("asks") or []:
                self.asks[Decimal(p)] = Decimal(q)
            self.valid = True
        elif et == "book_delta":
            if not self.valid:
                return
            self._apply_levels(self.bids, ev.get("bids") or [], positive_side=True)
            self._apply_levels(self.asks, ev.get("asks") or [], positive_side=False)
        if ev.get("sequence") is not None:
            self.sequence = ev["sequence"]

    @staticmethod
    def _apply_levels(side_map, levels, positive_side=True):
        for p, q in levels:
            price = Decimal(p)
            qty = Decimal(q)
            if qty == 0:
                side_map.pop(price, None)
            else:
                side_map[price] = qty

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def mid(self):
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
    ob = OrderBook()
    rows = []
    for _, ev in df.iterrows():
        et = ev["event_type"]
        if et == "bbo":
            # BBO-событие: у нас одна сторона в price/qty, обе — в payload (формат биржи)
            mid = _bbo_mid_from_payload(ev)
            if mid is not None:
                rows.append((ev["recv_monotonic_ns"], ev["recv_utc_ns"], mid, 0))
            continue
        if et in ("book_snapshot", "book_delta"):
            ob.apply(ev.to_dict())
            mid = ob.mid()
            if mid is not None:
                rows.append((ev["recv_monotonic_ns"], ev["recv_utc_ns"], float(mid), 0))
    out = pd.DataFrame(rows, columns=["mono_ns", "utc_ns", "mid", "age_s"])
    return out.dropna(subset=["mid"]).sort_values("mono_ns").reset_index(drop=True)


def _bbo_mid_from_payload(ev) -> float:
    """Извлечь mid из payload BBO-события — единый контракт bid_price/ask_price (ревью P0-4)."""
    pl = ev.get("payload")
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
    if bid <= 0 or ask <= 0 or bid > ask:
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
"""Paper-симулятор v2 (ревью P1-6/P1-7/P1-9): R строго из трёх ВНЕШНИХ источников,
порог шума по прошлым данным, подтверждение 2/3 без ложного oppose, one-module R/b/F.
Симуляция исполнения — по исходным событиям (не сетке). ВИРТУАЛЬНО, без плеча/шорта."""
import math
import statistics
from bisect import bisect_right
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from analysis.oracle import Oracle, build_oracle_series, premium_series, F_from_premium


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
    adverse_bps: float
    reach_raw: bool
    reach_adj: bool


class OrderBookReplay:
    """Последовательный replay стакана SafeTrade: as-of снимки без будущего."""

    def __init__(self, events_by_mono: list):
        self.events = events_by_mono
        self._times = [e["recv_monotonic_ns"] for e in events_by_mono]

    def asof_ns(self, t_ns: int, max_age_s: float = 5.0):
        from analysis.book import OrderBook
        idx = bisect_right(self._times, t_ns) - 1
        if idx < 0 or (t_ns - self._times[idx]) / 1e9 > max_age_s:
            return None
        ob = OrderBook()
        # от последнего снапшота до idx (кэш снапшотов — TODO оптимизация после корректности)
        last_snap = -1
        for i in range(idx, -1, -1):
            if self.events[i]["event_type"] == "book_snapshot":
                last_snap = i
                break
        for i in range(max(last_snap, 0), idx + 1):
            ob.apply(self.events[i])
        return ob


class PaperSimulator:
    def __init__(self, cfg: dict, symbols: list):
        self.cfg = cfg
        self.paper_cfg = cfg["paper"]
        fees = cfg.get("fees", {}).get("safetrade", {}).get("taker_bps", 0.0)
        self.fees = fees / 1e4   # доля
        self.sources = cfg["sources"]                              # ТОЛЬКО внешние (ревью)
        self.symbols = symbols
        self._premium_t = None
        self._premium_b = None

    # ================= сигналы (causal) =================
    def detect_signals(self, mid_series_by_ex: dict, t0_ns: int, t1_ns: int, W_s: int, D_s: int):
        """Движение R за W только по внешним; подтверждение >=2/3; порог по прошлому.
        Возвращает (signals, funnel) — воронка причин отказа (ревью п.8)."""
        funnel = {"no_3of3": 0, "no_source_data": 0, "weak_move": 0, "disagree": 0, "spread": 0, "signal": 0}
        # только внешние источники для R (ревью P1-6)
        ext = {k: v for k, v in mid_series_by_ex.items() if k in self.sources}
        if len(ext) < 3:
            return [], funnel
        # событийный R-ряд по единому оракулу
        r_series = self._build_r_series(ext)   # np.array Nx3
        if r_series is None or len(r_series) < 50:
            return [], funnel
        t_r = r_series[:, 0].astype(int)
        r = r_series[:, 1]
        n_src = r_series[:, 2].astype(int)
        W_ns = W_s * 1e9

        # порог шума: только по данным ДО текущей точки (прошлое rolling-окно) (ревью P1-7)
        i0 = int(np.searchsorted(t_r, t0_ns))
        i1 = min(int(np.searchsorted(t_r, t1_ns)), len(t_r))
        signals = []
        cooldown_until = 0
        for j in range(i0 + 1, i1):
            t_j = t_r[j]
            if t_j < cooldown_until:
                continue
            if n_src[j] < 3:
                funnel["no_3of3"] += 1
                continue
            i = int(np.searchsorted(t_r, t_j - W_ns))
            if i >= j - 1:
                funnel["weak_move"] += 1
                continue
            r0j, r1j = r[i], r[j]
            if r0j <= 0:
                continue
            d = math.log(r1j / r0j)
            thr = self._past_noise_threshold(r_series, i, j, W_s)  # по прошлому
            if abs(d) < thr:
                funnel["weak_move"] += 1
                continue
            direction = "up" if d > 0 else "down"
            agrees, opposes, per = self._confirm(ext, t_j, W_ns, direction, thr)
            if agrees < self.paper_cfg["confirm_min_sources"] or opposes >= 1:
                any_no_data = any(p[1] == "no_data" for p in per)
                if any_no_data:
                    funnel["no_source_data"] += 1
                else:
                    funnel["disagree"] += 1
                continue
            if not self._spread_ok(ext, t_j):
                funnel["spread"] += 1
                continue
            b, _ = self._premium_asof(t_j)
            F0 = F_from_premium(float(r1j), b)
            signals.append(Signal(t0_ns=int(t_j), R0=float(r1j), F0=F0, direction=direction,
                                  n_confirm=agrees, per_source=per, b=b, W_s=W_s))
            funnel["signal"] += 1
            cooldown_until = t_j + self.paper_cfg["cooldown_s"] * 1e9
        return signals, funnel

    def _build_r_series(self, ext: dict):
        """Потоковый merge всех событий внешних источников; на каждом — R (as-of, 3/3)."""
        rows = []
        for ex, s in ext.items():
            if len(s) < 10:
                return None
            t = s["mono_ns"].values
            m = s["mid"].values
            for k in range(len(t)):
                rows.append((int(t[k]), ex, float(m[k])))
        rows.sort(key=lambda x: x[0])
        oracle = Oracle(list(ext.keys()), max_bbo_age_s=self.cfg["fitness"]["max_bbo_age_s"],
                        min_sources=3)
        out_t, out_r, out_n = [], [], []
        for t_ns, ex, mid in rows:
            oracle.feed(ex, t_ns, mid)
            R, n, ids = oracle.mid_at(t_ns)
            if R is not None:
                out_t.append(t_ns)
                out_r.append(R)
                out_n.append(n)
        if len(out_t) < 50:
            return None
        return np.array([out_t, out_r, out_n]).T

    def _past_noise_threshold(self, r_series, i, j, W_s):
        """MAD изменений R за [t_j - X, t_j), X = 10*W (прошлое). Абс. нижний порог из конфига."""
        mad_mult = self.paper_cfg["noise"]["mad_mult"]
        abs_min = self.paper_cfg["noise"]["abs_min_bps"] / 1e4
        t_j = r_series[j, 0]
        x_ns = max(10 * W_s, 60) * 1e9
        i0 = int(np.searchsorted(r_series[:, 0], t_j - x_ns))
        if j - i0 < 20:
            return abs_min
        rr = r_series[i0:j, 1].astype(float)
        diffs = np.diff(np.log(rr))
        if len(diffs) < 10:
            return abs_min
        mad = float(np.median(np.abs(diffs - np.median(diffs))))
        return max(mad_mult * mad, abs_min)

    def _confirm(self, ext, t_ns, W_ns, direction, thr):
        """2/3 подтверждение; слабое движение В ТУ ЖЕ сторону — neutral (ревью P1-7).
        Слабое движение в противоположную сторону, превосходящее 0.25*thr — oppose."""
        agrees, opposes, per = 0, 0, []
        for ex, s in ext.items():
            t = s["mono_ns"].values
            j = int(np.searchsorted(t, t_ns)) - 1
            i = int(np.searchsorted(t, t_ns - W_ns)) - 1
            if i < 0 or j <= i or j >= len(s):
                per.append((ex, "no_data"))
                continue
            mj, mi = float(s.iloc[j]["mid"]), float(s.iloc[i]["mid"])
            if mi <= 0:
                per.append((ex, "no_data")); continue
            d = math.log(mj / mi)
            same_dir = (d > 0) == (direction == "up")
            if same_dir and abs(d) >= thr * 0.5:
                agrees += 1; per.append((ex, "agree"))
            elif same_dir and abs(d) < thr * 0.5:
                per.append((ex, "weak_same"))       # neutral
            elif abs(d) <= thr * 0.25:
                per.append((ex, "neutral"))
            else:
                opposes += 1; per.append((ex, "oppose"))
        return agrees, opposes, per

    def _spread_ok(self, ext, t_ns, max_spread_bps=15.0):
        vals = []
        for ex, s in ext.items():
            idx = int(np.searchsorted(s["mono_ns"].values, t_ns)) - 1
            if 0 <= idx < len(s):
                vals.append(float(s.iloc[idx]["mid"]))
        if len(vals) < 2:
            return False
        med = float(np.median(vals))
        spread = (max(vals) - min(vals)) / med * 1e4
        return spread <= max_spread_bps

    def _premium_asof(self, t_ns):
        if self._premium_t is None or len(self._premium_t) == 0:
            return None, 0
        idx = int(np.searchsorted(self._premium_t, t_ns)) - 1
        if idx < 0:
            return None, 0
        return float(self._premium_b[idx]), idx + 1

    def set_premium_series(self, t_arr, b_arr):
        self._premium_t = t_arr
        self._premium_b = b_arr

    # ================= исполнение (taker/taker) =================
    def run_hypothesis(self, signals, ob_replay, hypo, W_s, D_s, H_s, L_ms_list, sizes_usdt, symbol):
        trades, stats = [], {"n_signals": len(signals), "n_entries": 0, "n_trades": 0,
                             "by_L": {L: {"n": 0, "pnl": Decimal(0), "fees": Decimal(0)} for L in L_ms_list}}
        for sig in signals:
            # long-only (ревью P2): покупка только на росте
            if sig.direction != "up":
                continue
            for L_ms in L_ms_list:
                t_entry = sig.t0_ns + D_s * 1e9 + L_ms * 1e6
                ob = ob_replay.asof_ns(t_entry)
                if ob is None or not ob.valid:
                    continue
                for q_usdt in sizes_usdt:
                    tr = self._execute_one(sig, ob, ob_replay, hypo, W_s, D_s, H_s, L_ms, q_usdt, symbol)
                    if tr is None:
                        continue
                    trades.append(tr)
                    stats["n_entries"] += 1
                    stats["n_trades"] += 1
                    stats["by_L"][L_ms]["n"] += 1
                    stats["by_L"][L_ms]["pnl"] += tr.pnl
                    stats["by_L"][L_ms]["fees"] += (tr.fee_buy + tr.fee_sell)
        stats["pnl_sum"] = sum(v["pnl"] for v in stats["by_L"].values())
        stats["fees_sum"] = sum(v["fees"] for v in stats["by_L"].values())
        return {"hypo": hypo, "stats": stats, "trades": trades}

    def _execute_one(self, sig, ob_entry, ob_replay, hypo, W_s, D_s, H_s, L_ms, q_usdt, symbol):
        qty = Decimal(str(q_usdt)) / Decimal(str(sig.R0))
        f = Decimal(str(self.fees))
        vwap_buy, filled = ob_entry.vwap("ask", qty)
        if vwap_buy is None or filled <= 0:
            return None
        C_in = qty * vwap_buy * (1 + f)
        # экономический фильтр (ТЗ п.9): Potential = q*F0*(1-f_sell) - C_in - buffer
        buffer = Decimal(str(self.paper_cfg["buffer_bps"] / 1e4))
        F0 = Decimal(str(sig.F0)) if sig.F0 is not None else vwap_buy
        potential = qty * F0 * (1 - f) - C_in - buffer * C_in
        if potential <= 0:
            return None
        t_exit = sig.t0_ns + D_s * 1e9 + L_ms * 1e6 + H_s * 1e9   # H от фактического входа
        ob_exit = ob_replay.asof_ns(t_exit)
        if ob_exit is None or not ob_exit.valid:
            return None   # TODO P2: неизвестный выход сохранять, а не выбрасывать
        vwap_sell, filled_sell = ob_exit.vwap("bid", qty)
        if vwap_sell is None or filled_sell <= 0:
            return None
        proceeds = qty * vwap_sell * (1 - f)
        pnl = proceeds - C_in
        adverse = None
        reach_raw = float(vwap_sell) >= float(sig.R0) * (1 - 1e-4)
        reach_adj = sig.F0 is not None and float(vwap_sell) >= float(sig.F0) * (1 - 1e-4)
        return Trade(symbol=symbol, hypo=hypo, W_s=W_s, D_s=D_s, H_s=H_s, L_ms=L_ms,
                     size_usdt=q_usdt, signal_utc_ns=sig.t0_ns,
                     entry_t_ns=int(sig.t0_ns + D_s * 1e9 + L_ms * 1e6), exit_t_ns=int(t_exit),
                     entry_vwap=vwap_buy, exit_vwap=vwap_sell, qty=qty,
                     fee_buy=C_in - qty * vwap_buy, fee_sell=proceeds * f / (1 - f) if False else qty * vwap_sell * f,
                     pnl=pnl, exit_reason="H_timeout", adverse_bps=adverse,
                     reach_raw=reach_raw, reach_adj=reach_adj)
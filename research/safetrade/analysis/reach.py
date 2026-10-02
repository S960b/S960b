"""Тесты достижения цели (ревью п.10).
A — информационный: достигает ли mid SafeTrade R0 (сырой) и F0 (скорректированной) цели.
B — исполнимый: q*sell_VWAP*(1-f_sell) >= C_in*(1+target_net_return).
Статусы раздельные на raw и adj: reached/timeout/unknown (конец записи ИЛИ внутренний разрыв).
Не смешивать mid/bid/VWAP."""
import math
import statistics
from bisect import bisect_right
from decimal import Decimal


def _status_for(ts_first, events, last_ts, limit, idx0):
    """Общий статус для горизонта: reached если нашли, unknown если данных нет до конца
    горизонта (запись закончилась или внутренний разрыв), иначе timeout."""
    if ts_first is not None:
        return "reached"
    # есть ли хоть одно событие после t0 в пределах горизонта? если НЕТ (пусто/конец) — unknown
    if idx0 >= len(events) or events[-1][0] < limit:
        return "unknown"
    return "timeout"


def test_A_reach(safe_book, R0: float, F0: float, t0_ns: int, horizons_s: list,
                 tol_bps: float, direction: str = "up"):
    """Достигает ли mid SafeTrade R0 или F0 в пределах tol_bps. Возвращает dict по горизонтам:
    {h: {"raw": t_s|None, "adj": t_s|None, "status_raw": ..., "status_adj": ...}}.
    unknown = данных нет (конец записи или внутренний разрыв). Проверка: цель НЕ должна быть
    достигнута уже на t0 (иначе сигнал не валиден как 'рост впереди')."""
    events = safe_book["mid_ts"]          # list of (mono_ns, mid)
    idx0 = bisect_right([e[0] for e in events], t0_ns)   # первое событие СТРОГО после t0
    mid0 = safe_book.get("mid_at_t0")
    out = {}
    if mid0 is not None:
        # если цель уже достигнута в t0 — сигнал невалиден как движение (ревью п.10)
        if direction == "up" and mid0 >= R0 * (1 - tol_bps / 1e4):
            for h in horizons_s:
                out[h] = {"raw": None, "adj": None, "status_raw": "already_at_target", "status_adj": "already_at_target"}
            return out
    for h in horizons_s:
        limit = t0_ns + h * 1e9
        raw_t = adj_t = None
        for i in range(idx0, len(events)):
            ts, mid = events[i]
            if ts > limit:
                break
            if direction == "up":
                if raw_t is None and mid >= R0 * (1 - tol_bps / 1e4):
                    raw_t = (ts - t0_ns) / 1e9
                if F0 is not None and adj_t is None and mid >= F0 * (1 - tol_bps / 1e4):
                    adj_t = (ts - t0_ns) / 1e9
            else:
                if raw_t is None and mid <= R0 * (1 + tol_bps / 1e4):
                    raw_t = (ts - t0_ns) / 1e9
                if F0 is not None and adj_t is None and mid <= F0 * (1 + tol_bps / 1e4):
                    adj_t = (ts - t0_ns) / 1e9
            if raw_t is not None and (adj_t is not None or F0 is None):
                break
        last_before_limit = events[-1][0] if events else 0
        out[h] = {
            "raw": raw_t, "adj": adj_t,
            "status_raw": _status_for(raw_t, events, last_before_limit, limit, idx0),
            "status_adj": _status_for(adj_t, events, last_before_limit, limit, idx0),
        }
    return out


def test_B_executable(safe_book, qty: Decimal, entry_vwap: Decimal, t0_ns: int, horizons_s: list,
                      f_buy_bps: float, f_sell_bps: float, target_net_bps: float = 0.0):
    """Исполнимый тест (ревью п.10): C_in = q*entry*(1+f_buy).
    Выход: q*sell_vwap*(1-f_sell) >= C_in*(1+target_net_bps/1e4).
    bids_vwap_ts: список (mono_ns, sell_vwap_for_qty) — VWAP по БИД-стороне стакана."""
    events = safe_book["bids_vwap_ts"]
    idx0 = bisect_right([e[0] for e in events], t0_ns)
    C_in = qty * entry_vwap * (1 + Decimal(str(f_buy_bps)) / 10000)
    target_net = Decimal(1) + Decimal(str(target_net_bps)) / 10000
    out = {}
    for h in horizons_s:
        limit = t0_ns + h * 1e9
        found = None
        for i in range(idx0, len(events)):
            ts, vwap = events[i]
            if ts > limit:
                break
            if vwap is not None:
                vwap_d = Decimal(str(vwap))
                if qty * vwap_d * (1 - Decimal(str(f_sell_bps)) / 10000) >= C_in * target_net:
                    found = (ts - t0_ns) / 1e9
                    break
        stat = "reached" if found is not None else ("unknown" if events[-1][0] < limit else "timeout")
        out[h] = {"exec": found, "status_exec": stat}
    return out


def summarize_reach(results: dict, horizons_s: list, key: str = "raw"):
    """Статистика статусов по горизонтам: %, timeout, unknown, медиана/p90."""
    out = {}
    for h in horizons_s:
        vals = [r[h] for r in results.values() if r is not None and isinstance(r.get(h), dict)]
        reached = [v.get(key) for v in vals
                   if v.get(f"status_{key}" if f"status_{key}" in v else "status_raw") == "reached"
                   and v.get(key) is not None]
        n = len(vals)
        statuses = {}
        for v in vals:
            s = v.get(f"status_{key}") if f"status_{key}" in v else v.get("status_raw")
            statuses[s] = statuses.get(s, 0) + 1
        out[h] = {
            "n_events": n,
            "reached_pct": round(len(reached) / n * 100, 1) if n else None,
            "by_status": statuses,
            "median_s": statistics.median(reached) if reached else None,
            "p90_s": _pct(reached, 0.9) if reached else None,
        }
    return out


def _pct(xs, q):
    xs_sorted = sorted(xs)
    k = int((len(xs_sorted) - 1) * q)
    return xs_sorted[k]
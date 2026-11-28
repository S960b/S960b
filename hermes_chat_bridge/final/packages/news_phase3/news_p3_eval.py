"""Read-only, fee-scenario BTC paper-study evaluation; no network or trading."""
from collections import Counter
import math
import statistics

HORIZONS = (1, 4, 24)
ROUND_TRIP_FEES = (0.001, 0.002, 0.003)


def _mean(items):
    return statistics.mean(items) if items else None


def _median(items):
    return statistics.median(items) if items else None


def _lag1_correlation(xs):
    # Overlapping BTC returns violate iid assumptions. Lag-1 is diagnostic only.
    if len(xs) < 4:
        return None
    a, b = xs[:-1], xs[1:]
    ma, mb = statistics.mean(a), statistics.mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    return (sum((x-ma)*(y-mb) for x,y in zip(a,b)) / math.sqrt(va*vb)
            if va > 0 and vb > 0 else None)


def _overlap_pairs(intervals):
    intervals = sorted(intervals)
    total = 0
    for i, (start, finish) in enumerate(intervals):
        for other_start, _ in intervals[i+1:]:
            if other_start >= finish:
                break
            total += 1
    return total


def _summary(nums):
    return {"n": len(nums), "mean_pct": round(100 * _mean(nums), 5) if nums else None,
            "median_pct": round(100 * _median(nums), 5) if nums else None,
            "positive_fraction": round(sum(x > 0 for x in nums) / len(nums), 5) if nums else None}


def evaluate(c):
    """Every metric is recomputed from raw BTC return, not legacy fixed-cost fields.

    The single fixed direction baseline is ALWAYS_SHORT: a non-adaptive
    short in each observation window. HODL means long BTC over that same
    entry/exit window, not a continuous portfolio position.
    """
    rows = c.execute("""SELECT p.id,p.entry_ms,p.direction,p.confidence,p.model,
                               o.horizon_h,o.exit_ms,o.btc_return
                        FROM outcomes AS o JOIN predictions AS p ON p.id=o.prediction_id
                        ORDER BY o.horizon_h,p.entry_ms,p.id""").fetchall()
    counts = dict(c.execute("SELECT status,COUNT(*) FROM mt_batches GROUP BY status").fetchall())
    total_predictions = c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    total_attempts = c.execute("SELECT COUNT(*) FROM events WHERE kind='model_attempt'").fetchone()[0]
    result = {"experiment": "news-phase3", "paper_only": True,
              "predictions": total_predictions, "model_attempts": total_attempts,
              "batch_status_counts": counts,
              "fees_round_trip": list(ROUND_TRIP_FEES),
              "fee_scope": "combined entry+exit fees and hypothetical slippage; funding, borrow and impact excluded",
              "horizons": [],
              "warnings": ["Windows may overlap. Means, win rates and lag-1 correlations "
                           "are descriptive, not proof of independence or profitability.",
                           "No real orders, execution, funding or slippage measurements."]}
    for h in HORIZONS:
        observed = [(entry, direction, confidence, model, exit_ms, float(raw))
                    for _, entry, direction, confidence, model, horizon, exit_ms, raw in rows if horizon == h]
        model_gross = []
        hodl_gross = []
        intervals = []
        directions = []
        for entry, direction, confidence, model, exit_ms, raw in observed:
            if not math.isfinite(raw):
                raise ValueError("NONFINITE_OUTCOME_RETURN")
            if direction not in ("UP", "DOWN", "NEUTRAL"):
                raise ValueError("INVALID_SAVED_DIRECTION")
            pos = {"UP": 1, "DOWN": -1, "NEUTRAL": 0}[direction]
            model_gross.append(pos * raw)
            hodl_gross.append(raw)
            intervals.append((entry, entry + h*3600_000))
            if pos:
                directions.append((pos > 0) == (raw > 0) and raw != 0)
        fee_scenarios = {}
        for fee in ROUND_TRIP_FEES:
            net = [g - (fee if d != "NEUTRAL" else 0)
                   for g, (_,d,_,_,_,_) in zip(model_gross,observed)]
            fee_scenarios[f"{fee:.3f}"] = {
                "model": _summary(net),
                "btc_hodl": _summary([g-fee for g in hodl_gross]),
                "always_short": _summary([-g-fee for g in hodl_gross]),
                "cash": _summary([0.0 for _ in observed]),
            }
        result["horizons"].append({
            "horizon_h": h, "expected": total_predictions, "completed": len(observed),
            "model_gross": _summary(model_gross),
            "btc_hodl_gross": _summary(hodl_gross),
            "directional_evaluated": len(directions),
            "directional_accuracy": round(sum(directions)/len(directions), 5) if directions else None,
            "neutral_count": sum(x[1]=="NEUTRAL" for x in observed),
            "overlapping_window_pairs": _overlap_pairs(intervals),
            "btc_lag1_correlation": _lag1_correlation(hodl_gross),
            "cost_scenarios": fee_scenarios,
        })
    return result

"""Observed target hits and censored timeouts; raw and adjusted targets are separate."""
import math
import statistics
from bisect import bisect_right
from decimal import Decimal


def _covered(events, t0, limit, max_age_s, record_end, baseline_known):
    if record_end < limit or not baseline_known:
        return False
    # A message is evidence only while it remains fresh. Silence is not a price path.
    times = [int(e[0]) for e in events]
    lo = bisect_right(times, t0) - 1
    last = t0 if lo < 0 else times[lo]
    for ts, value in events[max(lo + 1, 0):]:
        if ts > limit:
            break
        if (ts - last) / 1e9 > max_age_s or value is None or not math.isfinite(float(value)):
            return False
        last = ts
    return (limit - last) / 1e9 <= max_age_s


def test_A_reach(safe_book, R0, F0, t0_ns, horizons_s, tol_bps, direction='up'):
    events = safe_book['mid_ts']
    times = [int(e[0]) for e in events]
    k = bisect_right(times, t0_ns) - 1
    age = safe_book.get('max_age_s', 5.0)
    mid0 = safe_book.get('mid_at_t0')
    if mid0 is None and k >= 0 and (t0_ns - times[k]) / 1e9 <= age:
        mid0 = events[k][1]
    baseline = mid0 is not None and math.isfinite(float(mid0))
    record_end = safe_book.get('record_end_ns', times[-1] if times else 0)
    out = {}
    for h in horizons_s:
        limit = int(t0_ns + h * 1e9)
        row = {}
        for name, target in [('raw', R0), ('adj', F0)]:
            found = None
            def hit(m):
                return (float(m) >= target * (1 - tol_bps / 1e4) if direction == 'up'
                        else float(m) <= target * (1 + tol_bps / 1e4))
            if target is None:
                status = 'unavailable'
            elif not baseline:
                status = 'baseline_unknown'
            elif hit(mid0):
                status = 'already_at_target'
            else:
                for ts, mid in events[k + 1:]:
                    if ts <= t0_ns:
                        continue
                    if ts > limit:
                        break
                    if mid is not None and math.isfinite(float(mid)) and hit(mid):
                        found = (ts - t0_ns) / 1e9
                        break
                status = ('reached' if found is not None else
                          'timeout' if _covered(events, t0_ns, limit, age, record_end, baseline) else 'unknown')
            row[name] = found
            row['status_' + name] = status
        out[h] = row
    return out


def test_B_executable(safe_book, qty, entry_vwap, t0_ns, horizons_s,
                      f_buy_bps, f_sell_bps, target_net_bps=0.0):
    # Caller must build bids_vwap_ts for this EXACT qty with complete depth.
    events = safe_book['bids_vwap_ts']
    cost = qty * entry_vwap * (1 + Decimal(str(f_buy_bps)) / 10000)
    target = cost * (1 + Decimal(str(target_net_bps)) / 10000)
    out = {}
    for h in horizons_s:
        limit = int(t0_ns + h * 1e9)
        found = None
        for ts, vwap in events:
            if ts <= t0_ns:
                continue
            if ts > limit:
                break
            if vwap is not None and math.isfinite(float(vwap)):
                if qty * Decimal(str(vwap)) * (1 - Decimal(str(f_sell_bps)) / 10000) >= target:
                    found = (ts - t0_ns) / 1e9
                    break
        end = safe_book.get('record_end_ns', events[-1][0] if events else 0)
        covered = _covered(events, t0_ns, limit, safe_book.get('max_age_s', 5), end, True)
        out[h] = {'exec': found, 'status_exec': 'reached' if found is not None else 'timeout' if covered else 'unknown'}
    return out


def _quantile(sorted_vals, q):
    """Линейная интерполяция квантиля (как np.quantile method='linear')."""
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return sorted_vals[lo]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


def summarize_reach(results, horizons_s, key='raw'):
    out = {}
    for h in horizons_s:
        vals = [r[h] for r in results.values() if r is not None and isinstance(r.get(h), dict)]
        statuses = {}
        reached = []
        for v in vals:
            s = v.get('status_' + key, 'unknown')
            statuses[s] = statuses.get(s, 0) + 1
            if s == 'reached' and v.get(key) is not None:
                reached.append(v[key])
        n = len(vals)
        observed = statuses.get('reached', 0) + statuses.get('timeout', 0)
        row = {'n_events': n, 'n_observed_outcomes': observed, 'by_status': statuses,
               'reached_pct': round(len(reached) / n * 100, 1) if n else None,
               'reached_observed_pct': round(len(reached) / observed * 100, 1) if observed else None,
               'median_s': statistics.median(reached) if reached else None,
               'p90_s': _quantile(sorted(reached), 0.9) if reached else None,
               'reached_times_list': [round(x, 2) for x in sorted(reached)] if reached else []}
        if reached:
            # при малом n p90 ненадёжен — показываем сами времена и n
            row['p90_note'] = (f'p90 по {len(reached)} наблюдениям; '
                               f'времена: {row["reached_times_list"]}')
        out[h] = row
    return out

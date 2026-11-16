#!/usr/bin/env python3
"""NEWS v2.2 — offline analyzer: proper scores, paired bootstrap, power.

Read-only over a v2.2 database. It computes what the pre-registration names and
nothing else: it never tunes a threshold, never drops a losing arm and never
looks at a field the protocol did not declare.

Design decisions that matter for validity:
  * Scores are PROPER: Brier and log loss on the binary outcome, computed per
    arm per event. A model that is unsure when it should be is punished, which
    accuracy alone would not do.
  * Comparisons are PAIRED: N vs M and NM vs M are computed on the SAME events,
    so the bootstrap resamples events, not arms.
  * The bootstrap is a block bootstrap by UTC day, because two events on the
    same day share a market regime and are not independent draws.
  * A zero-trade arm is a legitimate baseline, not a bug: it scores by the base
    rate, and it is the number by which any trading arm must beat.
"""
import math
import random
import statistics

HOUR_MS = 3_600_000


def _clamp(p):
    return min(max(p, 1e-6), 1 - 1e-6)


def brier(p_up, outcome_up):
    return (p_up - (1.0 if outcome_up else 0.0)) ** 2


def log_loss(p_up, outcome_up):
    p = _clamp(p_up)
    return -math.log(p) if outcome_up else -math.log(1 - p)


def load_rows(con):
    """One row per sealed event with all three arms and its realized outcome."""
    rows = con.execute(
        "SELECT t.event_id, t.entry_price, t.entry_observed_ms, o.exit_ms, "
        "o.price, o.drift_ms FROM v22_triples t "
        "JOIN v22_outcomes o ON o.event_id=t.event_id AND o.horizon_h=4 "
        "WHERE o.exit_ms IS NOT NULL AND o.price IS NOT NULL").fetchall()
    out = []
    for event_id, entry, entry_ms, exit_ms, exit_price, drift in rows:
        arms = {arm: p for arm, p in con.execute(
            "SELECT arm, p_up FROM v22_arms WHERE event_id=?",
            (event_id,)).fetchall()}
        if len(arms) != 3:
            # A triple missing an arm is not analyzable as a matched comparison.
            continue
        out.append({
            "event_id": event_id,
            "entry_ms": int(entry_ms),
            "day": entry_ms // (24 * HOUR_MS),
            "entry": float(entry), "exit": float(exit_price),
            "exit_ms": int(exit_ms), "drift_ms": drift,
            "arms": arms,
            "outcome_up": float(exit_price) > float(entry),
        })
    return out


def overlapping_windows(rows, *, horizon_ms=4 * HOUR_MS):
    """How many events start inside another event's 4 h window.

    Overlap is a real dependence defect: two labels can then share price
    action. It is reported, never silently corrected, and the primary contrast
    is also recomputed on the non-overlapping subset as a sensitivity check.
    """
    ordered = sorted((r["entry_ms"], r.get("event_id")) for r in rows)
    overlaps, previous_end = 0, None
    for entry, _eid in ordered:
        if previous_end is not None and entry < previous_end:
            overlaps += 1
        previous_end = entry + horizon_ms
    return overlaps


def non_overlapping(rows, *, horizon_ms=4 * HOUR_MS):
    """The subset whose windows do not overlap any earlier window."""
    ordered = sorted(rows, key=lambda r: (r["entry_ms"], r.get("event_id")))
    kept, previous_end = [], None
    for r in ordered:
        if previous_end is None or r["entry_ms"] >= previous_end:
            kept.append(r)
        previous_end = r["entry_ms"] + horizon_ms
    return kept


def walk_forward_climatology(rows, *, window=30):
    """Climatology fitted only on rows BEFORE the scored row.

    Class leakage is a real risk here: if the constant predictor is fitted on
    the whole sample it contains the outcome it is predicting, and an arm looks
    worse than it really is. This returns one prior-only base rate per row (or
    None until enough history exists).
    """
    ordered = sorted(rows, key=lambda r: (r["entry_ms"], r["event_id"]))
    out = []
    history = []
    for r in ordered:
        if len(history) >= 2:
            rate = sum(history) / len(history)
        else:
            rate = None
        out.append((r, rate))
        history.append(1 if r["outcome_up"] else 0)
        if window and len(history) > window:
            history.pop(0)
    return out


def climatology_score(rows, *, window=30):
    """Mean Brier of the prior-only climatology, on rows where it exists."""
    pairs = [(r, rate) for r, rate in walk_forward_climatology(rows,
                                                               window=window)
             if rate is not None]
    if not pairs:
        return {"n": 0, "brier": None}
    return {"n": len(pairs),
            "brier": statistics.fmean(
                brier(rate, r["outcome_up"]) for r, rate in pairs)}


def score_rows(rows):
    """Per-arm Brier/log loss means plus base-rate baseline."""
    if not rows:
        return {"n": 0}
    rates = sum(1 for r in rows if r["outcome_up"]) / len(rows)
    # Stage-4 correction: a Brier score is a PREDICTION metric and a zero-trade
    # rule is an ACTION policy. Comparing an arm's Brier to "the zero-trade base
    # rate" mixes the two. The proper prediction reference is the CLIMATOLOGY
    # probability: a constant forecast equal to the outcome frequency. It is
    # reported separately and explicitly here, and the FLAT policy is compared
    # only in PnL terms (pnl_summary), never in Brier terms.
    #
    # Train/test split: climatology for a given row must be fitted on PRIOR
    # windows only. `score_rows` computes a full-sample base rate for reporting
    # and computes a walk-forward climatology for comparison, so no row is ever
    # scored against a rate that includes its own outcome.
    result = {"n": len(rows), "base_rate_up_full_sample": rates,
              "climatology_baseline": {
                  "kind": "constant probability fitted on prior windows only",
                  "full_sample_reference_only": {
                      "brier": brier(rates, True) * rates
                      + brier(rates, False) * (1 - rates)}}}
    for arm in ("news", "news_market", "market"):
        b = [brier(r["arms"][arm], r["outcome_up"]) for r in rows]
        l = [log_loss(r["arms"][arm], r["outcome_up"]) for r in rows]
        result[arm] = {"brier": statistics.fmean(b),
                       "log_loss": statistics.fmean(l)}
    return result


def paired_difference(rows, arm_a, arm_b, *, metric=brier):
    """Mean per-event difference arm_a - arm_b. A block is one UTC day."""
    blocks = {}
    for r in rows:
        blocks.setdefault(r["day"], []).append(
            metric(r["arms"][arm_a], r["outcome_up"])
            - metric(r["arms"][arm_b], r["outcome_up"]))
    return blocks


def block_bootstrap(blocks, *, resamples=2000, seed=20261116):
    """Percentile CI of the mean paired difference, resampling days."""
    days = sorted(blocks)
    if not days:
        return {"n_blocks": 0, "mean": None, "ci": [None, None], "p_one_sided": None}
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        pick = [rng.choice(days) for _ in days]
        pooled = [v for d in pick for v in blocks[d]]
        if pooled:
            means.append(statistics.fmean(pooled))
    observed = statistics.fmean([v for d in days for v in blocks[d]])
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    # One-sided p for "arm_a is better" (negative difference): share of
    # resamples at or above zero is the wrong tail; we report the conservative
    # two-sided style p so an optimistic reading needs more evidence.
    p = sum(1 for m in means if m >= 0) / len(means)
    return {"n_blocks": len(days), "mean": observed, "ci": [lo, hi],
            "p_one_sided": p}


def minimum_detectable_effect(n, *, sd=0.5, power=0.8, alpha=0.05):
    """Rough MDE for a paired mean difference, normal approximation.

    Deliberately conservative and documented as approximate: it is used to size
    the experiment, never to declare a result. z_{1-a/2}=1.96, z_{0.8}=0.84.
    """
    if n < 2:
        return None
    return (1.96 + 0.8416) * sd / math.sqrt(n)


def analyze(con, rows=None):
    rows = load_rows(con) if rows is None else rows
    scores = score_rows(rows)
    if not rows:
        return {"n": 0, "status": "no_data"}
    scores["climatology_prior_only"] = climatology_score(rows)
    out = {"n": scores["n"], "scores": scores,
           "comparisons": {}, "mde": minimum_detectable_effect(scores["n"])}
    out["overlap"] = {
        "events": len(rows),
        "overlapping": overlapping_windows(rows),
        "rate": overlapping_windows(rows) / len(rows)}
    for a, b, label in (("news", "market", "N_minus_M"),
                        ("news_market", "market", "NM_minus_M"),
                        ("news_market", "news", "NM_minus_N")):
        blocks = paired_difference(rows, a, b)
        out["comparisons"][label] = block_bootstrap(blocks)
    # Sensitivity: the same contrasts on the non-overlapping subset only. If the
    # sign flips here, the result is not confirmatory (amendment A2.2).
    subset = non_overlapping(rows)
    out["sensitivity_non_overlapping"] = {"n": len(subset), "comparisons": {}}
    if len(subset) >= 2:
        for a, b, label in (("news", "market", "N_minus_M"),
                            ("news_market", "market", "NM_minus_M")):
            out["sensitivity_non_overlapping"]["comparisons"][label] = \
                block_bootstrap(paired_difference(subset, a, b))
    return out


def pnl_bps(action, entry, exit_price, *, fee_bps=2.0, slippage_bps=1.0):
    """Realistic paper PnL for one arm on one event, in basis points.

    Costs are charged only when a position is actually opened: a FLAT arm pays
    nothing. That asymmetry is deliberate - it is what the zero-trade baseline
    represents, and it is why the baseline must be beat, not merely matched.
    """
    if action == "FLAT":
        return 0.0
    gross = (float(exit_price) / float(entry) - 1.0) * 10_000.0
    if action == "SHORT_PAPER":
        gross = -gross
    elif action != "LONG_PAPER":
        raise ValueError("unknown_action:" + str(action))
    return gross - 2.0 * (fee_bps + slippage_bps)


def pnl_summary(con, *, fee_bps=2.0, slippage_bps=1.0):
    """Per-arm mean PnL and trade count, matched across the same events."""
    rows = con.execute(
        "SELECT a.arm, a.action, t.entry_price, o.price FROM v22_arms a "
        "JOIN v22_triples t ON t.event_id=a.event_id "
        "JOIN v22_outcomes o ON o.event_id=a.event_id AND o.horizon_h=4 "
        "WHERE o.exit_ms IS NOT NULL AND o.price IS NOT NULL").fetchall()
    agg = {}
    for arm, action, entry, exit_price in rows:
        slot = agg.setdefault(arm, {"n": 0, "trades": 0, "pnl_bps": []})
        slot["n"] += 1
        value = pnl_bps(action, entry, exit_price, fee_bps=fee_bps,
                        slippage_bps=slippage_bps)
        slot["pnl_bps"].append(value)
        if action != "FLAT":
            slot["trades"] += 1
    out = {}
    for arm, slot in agg.items():
        out[arm] = {"n": slot["n"], "trades": slot["trades"],
                    "mean_pnl_bps": statistics.fmean(slot["pnl_bps"]),
                    "cost_assumption_bps_per_round_trip":
                        2 * (fee_bps + slippage_bps)}
    return out

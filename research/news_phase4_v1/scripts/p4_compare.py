#!/usr/bin/env python3
"""NEWS Phase 4 v0.1: *offline*, point-in-time paired paper-study evaluator.

No model calls, network, filesystem writes, SQLite connection or trading.
Accepts only a bounded JSONL of already sealed comparable decision snapshots.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
import time

ARMS = ("news", "news_market", "news_market_social")
NEEDS = {
    "news": frozenset(("news",)),
    "news_market": frozenset(("news", "market")),
    "news_market_social": frozenset(("news", "market", "social")),
}
HORIZONS = (1, 4, 24)
FEES = (0.001, 0.002, 0.003)  # 0.10%, 0.20%, 0.30% round-trip
MILLISECONDS_PER_HOUR = 3_600_000
MAX_EVENTS = 5000
MAX_LINE_BYTES = 65_536
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
EVENT_ID = re.compile(r"[a-f0-9]{16,64}\Z")
MODEL = re.compile(r"[a-zA-Z0-9_.-]{2,100}/[a-zA-Z0-9_.-]{2,100}\Z")


class InvalidStudy(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise InvalidStudy(code)


def exact_keys(value, required, optional=()):
    require(isinstance(value, dict), "expected_object")
    keys = set(value)
    require(set(required) <= keys <= set(required) | set(optional), "unexpected_or_missing_keys")


def integer(value):
    return type(value) is int and value >= 0


def positive_price(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def valid_digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def validate_event(event, as_of_ms):
    exact_keys(event, ("event_id", "cutoff_ms", "entry_ms", "entry_price",
                       "features", "decisions", "outcomes"))
    require(isinstance(event["event_id"], str) and EVENT_ID.fullmatch(event["event_id"]), "invalid_event_id")
    cutoff, entry = event["cutoff_ms"], event["entry_ms"]
    require(integer(cutoff) and integer(entry) and cutoff <= entry <= as_of_ms, "invalid_event_clock")
    require(positive_price(event["entry_price"]), "invalid_entry_price")
    features = event["features"]
    require(isinstance(features, dict) and "news" in features, "news_feature_required")
    require(set(features) <= {"news", "market", "social"}, "invalid_feature_source")
    for name, feat in features.items():
        exact_keys(feat, ("available_ms", "digest"))
        require(integer(feat["available_ms"]) and feat["available_ms"] <= cutoff,
                "feature_future_leakage")
        require(valid_digest(feat["digest"]), "invalid_feature_digest")
    decisions = event["decisions"]
    require(isinstance(decisions, dict) and bool(decisions) and set(decisions) <= set(ARMS), "invalid_decisions")
    for arm, decision in decisions.items():
        exact_keys(decision, ("direction", "confidence", "model", "completed_ms", "input_hash"))
        require(NEEDS[arm] <= set(features), "arm_missing_feature")
        require(decision["direction"] in ("UP", "DOWN", "NEUTRAL"), "invalid_direction")
        confidence = decision["confidence"]
        require(type(confidence) in (float, int) and math.isfinite(confidence) and 0 <= confidence <= 1,
                "invalid_confidence")
        require(isinstance(decision["model"], str) and MODEL.fullmatch(decision["model"]), "unverified_model_identity")
        require(integer(decision["completed_ms"]) and cutoff <= decision["completed_ms"] <= entry,
                "entry_quote_must_follow_all_decisions")
        require(valid_digest(decision["input_hash"]), "invalid_input_hash")
    require(len({d["model"] for d in decisions.values()}) == 1, "model_confounded_between_arms")
    outcomes = event["outcomes"]
    require(isinstance(outcomes, list) and len(outcomes) <= 3, "invalid_outcomes")
    seen = set()
    for result in outcomes:
        exact_keys(result, ("horizon_h", "exit_ms", "exit_price", "settled_ms"))
        h = result["horizon_h"]
        require(type(h) is int and h in HORIZONS and h not in seen, "invalid_or_duplicate_horizon")
        seen.add(h)
        due_ms = entry + h * MILLISECONDS_PER_HOUR
        require(integer(result["exit_ms"]) and result["exit_ms"] >= due_ms, "future_price_lookahead")
        require(integer(result["settled_ms"]) and result["settled_ms"] >= max(result["exit_ms"], due_ms + 120_000),
                "outcome_before_safety_guard")
        require(positive_price(result["exit_price"]), "invalid_exit_price")
    return event


def load_jsonl(filename, as_of_ms):
    """Read bounded, UTF-8 strictly validated JSONL; never touch production SQLite."""
    require(isinstance(filename, str) and filename.endswith(".jsonl"), "jsonl_only")
    path = Path(filename)
    require(path.is_file() and not path.is_symlink(), "file_missing_or_symlink")
    require(path.stat().st_size <= MAX_EVENTS * MAX_LINE_BYTES, "input_too_large")
    events, ids = [], set()
    with path.open("rb") as handle:
        for raw in handle:
            require(len(raw) <= MAX_LINE_BYTES, "line_too_large")
            require(len(events) < MAX_EVENTS, "too_many_events")
            try:
                event = json.loads(raw.decode("utf-8"), parse_constant=lambda _: require(False, "nonfinite_json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise InvalidStudy("invalid_jsonl") from exc
            validate_event(event, as_of_ms)
            require(event["event_id"] not in ids, "duplicate_event")
            ids.add(event["event_id"])
            events.append(event)
    return events


def net(direction, change, fee):
    position = {"UP": 1, "DOWN": -1, "NEUTRAL": 0}[direction]
    return position * change - (fee if position else 0)


def average(values):
    return round(sum(values) / len(values), 8) if values else None


def active_results(events, horizon, as_of_ms):
    result = []
    for e in events:
        for label in e["outcomes"]:
            if label["horizon_h"] == horizon and label["settled_ms"] <= as_of_ms:
                result.append((e, label, label["exit_price"] / e["entry_price"] - 1.0))
    return result


def overlap_adjacent_count(rows, horizon):
    starts = sorted(r[0]["entry_ms"] for r in rows)
    return sum(b < a + horizon * MILLISECONDS_PER_HOUR for a, b in zip(starts, starts[1:]))


def evaluate(events, as_of_ms):
    require(integer(as_of_ms), "invalid_as_of_ms")
    ids = set()
    for event in events:
        validate_event(event, as_of_ms)
        require(event["event_id"] not in ids, "duplicate_event")
        ids.add(event["event_id"])
    summary = {"experiment": "news-phase4", "mode": "offline_paper_only",
               "events": len(events), "as_of_ms": as_of_ms,
               "arms": {}, "pairs": {},
               "roundtrip_cost_scenarios": list(FEES),
               "warnings": ["Model outputs and input timestamps are supplied evidence, not independently verified by this evaluator.",
                            "Overlapping outcome windows and clustered news violate IID assumptions.",
                            "No live trading, borrow/funding fees, market impact, taxes or execution quality measured."]}
    for arm in ARMS:
        supported = [e for e in events if NEEDS[arm] <= set(e["features"])]
        decided = [e for e in supported if arm in e["decisions"]]
        summary["arms"][arm] = {"feature_available": len(supported), "decisions": len(decided), "horizons": {}}
        for h in HORIZONS:
            rows = [(e, o, r) for e, o, r in active_results(decided, h, as_of_ms)]
            directional = [(e["decisions"][arm]["direction"], raw) for e, _, raw in rows
                           if e["decisions"][arm]["direction"] != "NEUTRAL"]
            metric = {"labeled": len(rows), "directional_decisions": len(directional),
                      "direction_accuracy": average([int((d == "UP" and raw > 0) or
                                                         (d == "DOWN" and raw < 0)) for d, raw in directional]),
                      "overlapping_adjacent_windows": overlap_adjacent_count(rows, h),
                      "mean_gross_return": average([net(e["decisions"][arm]["direction"], raw, 0) for e, _, raw in rows]),
                      "scenarios": {}}
            for fee in FEES:
                returns = [net(e["decisions"][arm]["direction"], raw, fee) for e, _, raw in rows]
                metric["scenarios"][f"{fee:.4f}"] = {
                    "mean_model_net": average(returns),
                    "model_positive_fraction": average([int(x > 0) for x in returns]),
                    "mean_hodl_net": average([raw - fee for _, _, raw in rows]),
                    "mean_always_short_net": average([-raw - fee for _, _, raw in rows]),
                    "mean_cash": 0.0 if rows else None,
                }
            summary["arms"][arm]["horizons"][str(h)] = metric
    for left, right in (("news", "news_market"), ("news_market", "news_market_social"),
                        ("news", "news_market_social")):
        key = f"{right}_minus_{left}"
        summary["pairs"][key] = {}
        for h in HORIZONS:
            rows = [(e, o, raw) for e, o, raw in active_results(events, h, as_of_ms)
                    if left in e["decisions"] and right in e["decisions"]]
            result = {"matched_events": len(rows),
                      "overlapping_adjacent_windows": overlap_adjacent_count(rows, h),
                      "mean_delta_net": {}}
            for fee in FEES:
                result["mean_delta_net"][f"{fee:.4f}"] = average([
                    net(e["decisions"][right]["direction"], raw, fee) -
                    net(e["decisions"][left]["direction"], raw, fee) for e, _, raw in rows])
            summary["pairs"][key][str(h)] = result
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Private sealed JSONL, NOT production SQLite")
    parser.add_argument("--as-of-ms", type=int, default=None)
    options = parser.parse_args()
    as_of = options.as_of_ms if options.as_of_ms is not None else int(time.time() * 1000)
    try:
        report = evaluate(load_jsonl(options.input, as_of), as_of)
    except (InvalidStudy, OSError) as exc:
        print(json.dumps({"status": "error", "code": str(exc) if isinstance(exc, InvalidStudy) else "file_io_failed"}), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

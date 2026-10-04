"""Independent offline contracts for opportunity.py and the revised watchdog.

No network, account access or real process signals. On 2467cb2 these expose
incorrect economics, direction/volume/TTL handling and process attribution.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maker.opportunity import opportunity_report, _round_amount
from maker.econ import econ_report

START = 1791052900
RID = "mk_opportunity_review"
PAIR = "PRLUSDT"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def snap(dt, bid="1.00", ask="1.04", bid_qty="100", extra_bids=None):
    return {"pair": PAIR, "t": int((START + dt) * 1e9), "ok": True,
            "bids": [[bid, bid_qty]] + (extra_bids or []), "asks": [[ask, "100"]]}


def trade(dt=5, price="1.00", amount="10", side="sell", tid=1):
    return {"id": tid, "created_at": iso(START + dt), "price": price,
            "amount": amount, "total": str(float(price) * float(amount)), "side": side}


def write_run(root, snapshots, trades, cutoff=60):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{RID}_manifest.json").write_text(json.dumps({
        "run_id": RID, "started_epoch": START, "started_utc": iso(START),
        "pairs": [PAIR], "params": {"tick_s": 20},
        "trade_side_semantics": "aggressor", "trade_side_status": "fixture_verified",
    }))
    polls = [{"pair": PAIR, "t": int((START + cutoff) * 1e9), "ok": True,
              "warmup": False, "coverage": "caught_up", "trades": trades}]
    for suffix, rows in (("depth", snapshots), ("trades", polls)):
        (root / f"{RID}_{suffix}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))


def result(root, snapshots=None, trades=None, cutoff=60, horizons=(10, 30)):
    write_run(root, snapshots or [snap(0), snap(20), snap(40), snap(60)],
              trades if trades is not None else [trade()], cutoff=cutoff)
    d = opportunity_report(str(root), RID, budgets=(5,), horizons=horizons,
                           cutoff_ns=int((START + cutoff) * 1e9), depth_tick_s=20)
    return d["pairs"][0]["by_budget"]["5"]


@pytest.mark.parametrize("pair,price,budget", [
    ("QUANTUSUSDT", 111.736, 5),
    ("PRLUSDT", 1.0, .5),
])
def test_rounding_cannot_spend_more_than_budget(pair, price, budget):
    try:
        qty = _round_amount(pair, budget / price)
    except ValueError:
        return  # explicit rejection of an invalid/minimum-incompatible proposal
    assert qty is None or qty * price <= budget + 1e-9, (qty, qty * price)


@pytest.mark.parametrize("side", ["buy", None])
def test_wrong_or_unknown_aggressor_is_not_a_directed_buy_opportunity(tmp_path, side):
    r = result(tmp_path, trades=[trade(side=side)])
    assert r["n_buy_touch"] == 0, r


def test_zero_volume_is_not_an_execution_opportunity(tmp_path):
    r = result(tmp_path, trades=[trade(amount="0")])
    assert r["n_touch"] == 0, r


def test_quote_expires_at_the_declared_ttl_during_a_data_gap(tmp_path):
    r = result(tmp_path, [snap(0), snap(100)], [trade(dt=80)], cutoff=100)
    assert r["n_touch"] == 0, r


def test_queue_is_compared_with_trade_volume_not_our_order_size(tmp_path):
    # The same-price sell trade exceeds the entire displayed queue plus our qty.
    # This is an opportunity under a static FIFO scenario, not a confirmed fill.
    r = result(tmp_path, [snap(0, ask="1.01", bid_qty="100"), snap(20)],
               [trade(amount="110")])
    assert r["n_buy_touch"] >= 1, r


def test_trade_on_snapshot_boundary_is_not_marked_out_twice(tmp_path):
    r = result(tmp_path, trades=[trade(dt=20)], horizons=(20,))
    observed = len(r["markout"].get("20", []))
    unobserved = r["unobserved"].get("20", 0)
    assert observed + unobserved == r["n_buy_touch"], r


def test_forced_exit_cost_has_consistent_sign_and_subtracts_fees_from_pnl(tmp_path):
    r = result(tmp_path)
    # Buy at 1.01, sell at 1.00, maker=taker=.001. Cost is positive loss.
    # A renamed net_pnl field would instead have the negative of this value.
    cost_bps = ((1.01 - 1.00) + .001 * (1.01 + 1.00)) / 1.01 * 1e4
    assert r["forced_exit_mt_bps"] == pytest.approx(round(cost_bps, 2)), r


def test_taker_exit_uses_all_consumed_bid_levels(tmp_path):
    deep = result(tmp_path / "deep", [snap(0), snap(20), snap(40), snap(60)])
    thin = result(tmp_path / "thin", [
        snap(dt, bid_qty="1", extra_bids=[[".90", "100"]])
        for dt in (0, 20, 40, 60)
    ])
    assert thin["forced_exit_mt_bps"] > deep["forced_exit_mt_bps"], (thin, deep)


def test_incomplete_exit_is_not_reported_as_full_quantity_cost(tmp_path):
    r = result(tmp_path, [snap(dt, bid_qty=".01") for dt in (0, 20, 40, 60)])
    assert r["forced_exit_mt_bps"] is None, r


def test_exit_after_touch_responds_to_the_observed_market_drop(tmp_path):
    steady = result(tmp_path / "steady", [snap(0), snap(20), snap(40), snap(60)])
    drop = result(tmp_path / "drop", [snap(0)] + [
        snap(dt, bid=".80", ask=".84") for dt in (20, 40, 60)
    ])
    assert drop["forced_exit_mt_bps"] > steady["forced_exit_mt_bps"], (drop, steady)


def test_two_tick_spread_cannot_improve_both_sides_into_a_self_cross(tmp_path):
    r = result(tmp_path, [snap(0, ask="1.02")], [], cutoff=20)
    # bid=1.00, ask=1.02, tick=.01: improving both creates buy=sell=1.01.
    # At least one side must remain queued, be omitted, or the proposal rejected.
    assert r.get("n_improved", 0) <= 1, r


def test_default_cutoff_is_not_mislabeled_as_explicit(tmp_path):
    write_run(tmp_path, [snap(0), snap(60)], [trade()])
    r = econ_report(str(tmp_path), RID)
    assert r["window_sources"] == "cutoff=конец данных", r["window_sources"]


def watchdog_helpers():
    spec = importlib.util.spec_from_file_location(
        "watchdog_review_helpers", ROOT / "tests" / "test_econ_watchdog_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_watchdog_does_not_signal_a_later_unrelated_collector(tmp_path):
    h = watchdog_helpers()
    h.shell_fixture(tmp_path, age=2 * 3600 + 65)
    p = tmp_path / "fakebin" / "ps"
    # The matching command belongs to a process started ~2h AFTER this run.
    p.write_text("#!/bin/sh\n" + "echo '" +
                 datetime.fromtimestamp(time.time() - 10).strftime("%a %b %d %H:%M:%S %Y") +
                 "'\n")
    p.chmod(0o755)
    (tmp_path / "data/maker" / f"{h.RID}_depth.jsonl").write_text(
        json.dumps(h.snapshot(time.time() - 300, "PRLUSDT")) + "\n")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "fakebin") + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["BASH_ENV"] = str(tmp_path / "bash_env")
    env["WATCHDOG_TEST_KILL_LOG"] = str(tmp_path / "mock_kill_calls")
    r = subprocess.run(["bash", str(tmp_path / "scripts" / h.WATCHDOG.name)],
                       env=env, text=True, capture_output=True, timeout=15)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "mock_kill_calls").exists(), "A different run was signaled"

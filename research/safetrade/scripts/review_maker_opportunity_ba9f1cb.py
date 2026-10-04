"""Independent offline scenarios for ba9f1cb. No network or real orders.

These test economic and queue contracts, rather than reported field presence.
All synthetic files live in pytest's temporary directories.
"""
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maker.opportunity import opportunity_report

START = 1791052900
RID = "mk_review_ba9f1cb"
PAIR = "PRLUSDT"


def iso(dt):
    return datetime.fromtimestamp(START + dt, timezone.utc).isoformat().replace("+00:00", "Z")


def snap(dt, bid="1.00", ask="1.04", bid_qty="100", ask_qty="100", extra_bids=()):
    return {"pair": PAIR, "t": (START + dt) * 10**9, "ok": True,
            "bids": [[bid, bid_qty]] + list(extra_bids), "asks": [[ask, ask_qty]]}


def trade(dt=5, price="1.00", amount="10", side="sell", tid=1):
    return {"id": tid, "created_at": iso(dt), "price": price, "amount": amount,
            "total": str(Decimal(price) * Decimal(amount)), "side": side}


def run(root, snapshots=None, trades=None, cutoff=60, semantics="aggressor", status="fixture_verified"):
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": RID, "started_epoch": START, "started_utc": iso(0),
                "pairs": [PAIR], "params": {"tick_s": 20}}
    if semantics is not None:
        manifest["trade_side_semantics"] = semantics
    if status is not None:
        manifest["trade_side_status"] = status
    snapshots = snapshots if snapshots is not None else [snap(dt) for dt in (0, 20, 40, 60)]
    trades = trades if trades is not None else [trade()]
    polls = [{"pair": PAIR, "t": (START + cutoff) * 10**9, "ok": True,
              "warmup": False, "coverage": "caught_up", "trades": trades}]
    (root / f"{RID}_manifest.json").write_text(json.dumps(manifest))
    for suffix, rows in (("depth", snapshots), ("trades", polls)):
        (root / f"{RID}_{suffix}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = opportunity_report(str(root), RID, cutoff_ns=(START + cutoff) * 10**9,
                                budgets=(5,), horizons=(10, 30), ttl_s=20)
    return report, report["pairs"][0]["by_budget"]["5"]


@pytest.mark.parametrize("semantics,status", [
    (None, None), ("aggressor", "unverified"), ("maker", "fixture_verified")])
def test_unverified_or_non_aggressor_side_cannot_enable_directed_opportunity(tmp_path, semantics, status):
    report, b = run(tmp_path, semantics=semantics, status=status)
    assert b["n_touch"] == 0, (report["policy"], b["n_touch"])
    assert b["n_price_match"] > 0  # diagnostic matching may still be reported


def test_sell_queue_uses_asks_not_bids(tmp_path):
    # Static FIFO at ask=1.01: volume 5 first consumes ask queue 2, leaving 3.
    snapshots = [snap(dt, ask="1.01", bid_qty="100", ask_qty="2") for dt in (0, 20, 40, 60)]
    _, b = run(tmp_path, snapshots, [trade(price="1.01", amount="5", side="buy")])
    assert b["potential_qty"] == pytest.approx([3.0]), b["potential_qty"]


def test_queue_depletes_across_trades_during_one_quote(tmp_path):
    snapshots = [snap(dt, ask="1.01", bid_qty="10") for dt in (0, 20, 40, 60)]
    trades = [trade(dt=5, amount="6", tid=1), trade(dt=10, amount="6", tid=2)]
    _, b = run(tmp_path, snapshots, trades)
    # First opposing trade consumes 6 of 10 ahead; the second consumes 4 then 2 ours.
    assert sum(b["potential_qty"]) == pytest.approx(2.0), b["potential_qty"]


def test_same_quote_cannot_allocate_more_than_its_quantity(tmp_path):
    _, b = run(tmp_path, trades=[trade(dt=5, amount="3", tid=1),
                                trade(dt=10, amount="3", tid=2)])
    assert len({a["quote_idx"] for a in b["audit"]}) == 1
    quoted_qty = b["audit"][0]["qty"]
    assert sum(b["potential_qty"]) <= quoted_qty + 1e-9, (b["potential_qty"], quoted_qty)


def test_zero_potential_quantity_does_not_generate_post_entry_exit_economics(tmp_path):
    snapshots = [snap(dt, ask="1.01", bid_qty="100") for dt in (0, 20, 40, 60)]
    _, b = run(tmp_path, snapshots, [trade(amount="1")])
    assert sum(b["potential_qty"]) == 0
    assert b["exit_qty_full"] == 0, (b["potential_qty"], b["exit_qty_full"])
    assert b["net_pnl_mt_bps"] is None


def test_partial_opportunity_exit_uses_actual_potential_quantity(tmp_path):
    # Only .01 could reach our improved bid. That .01 exits entirely at 1.00.
    # Selling the entire nominal ~4.95 instead consumes .90 and invents slippage.
    snapshots = [snap(dt, bid_qty=".01", extra_bids=[[".90", "100"]])
                 for dt in (0, 20, 40, 60)]
    _, b = run(tmp_path, snapshots, [trade(amount=".01")])
    expected = ((1.01 - 1.00) + .001 * (1.01 + 1.00)) / 1.01 * 1e4
    assert b["potential_qty"] == pytest.approx([.01])
    assert b["forced_exit_mt_bps"] == pytest.approx(round(expected, 2)), b["forced_exit_mt_bps"]


def test_profitable_exit_has_negative_cost_not_absolute_pnl(tmp_path):
    snapshots = [snap(0)] + [snap(dt, bid="1.03", ask="1.07") for dt in (20, 40, 60)]
    _, b = run(tmp_path, snapshots)
    assert b["net_pnl_mt_bps"] > 0
    assert b["forced_exit_mt_bps"] == pytest.approx(-b["net_pnl_mt_bps"]), b


def test_a_book_a_day_after_touch_cannot_be_an_observed_prompt_exit(tmp_path):
    _, b = run(tmp_path, [snap(0), snap(86400)], cutoff=86400)
    assert b["n_buy_touch"] == 1
    assert b["exit_qty_full"] == 0, b["exit_qty_full"]
    assert b["net_pnl_mt_bps"] is None

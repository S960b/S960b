"""Bounded offline follow-up: configuration, status, provenance and audit."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.review_maker_opportunity_ba9f1cb import run, snap, trade, START, RID
from maker.opportunity import opportunity_report


@pytest.mark.parametrize("status", ["", False, "rejected"])
def test_explicitly_unconfirmed_status_does_not_open_side_gate(tmp_path, status):
    report, b = run(tmp_path, status=status)
    assert report["data_quality"]["side_confirmed"] is False
    assert b["n_touch"] == 0


def test_requested_exit_delay_is_used_not_only_written_to_policy(tmp_path):
    run(tmp_path, [snap(dt) for dt in (0, 20, 40, 60)], [trade(dt=5)])
    report = opportunity_report(str(tmp_path), RID, cutoff_ns=(START + 60) * 10**9,
                                budgets=(5,), max_exit_delay_s=1)
    b = report["pairs"][0]["by_budget"]["5"]
    assert report["policy"]["max_exit_delay_s"] == 1
    assert b["exit_qty_full"] == 0
    assert b["exit_qty_unobserved"] == 1
    assert b["net_pnl_mt_bps"] is None


def test_audit_retains_actual_source_trade_id(tmp_path):
    _, b = run(tmp_path, trades=[trade(tid=712345)])
    assert b["n_buy_touch"] == 1
    assert b["audit"]
    assert b["audit"][0].get("trade_id") == 712345, b["audit"]


def test_collector_version_is_not_reported_as_analyzer_version(tmp_path):
    run(tmp_path)
    p = tmp_path / f"{RID}_manifest.json"
    manifest = json.loads(p.read_text())
    manifest["code_commit"] = "synthetic_collector_version"
    p.write_text(json.dumps(manifest))
    report = opportunity_report(str(tmp_path), RID, cutoff_ns=(START + 60) * 10**9,
                                budgets=(5,))
    assert report.get("analyzer_commit") != "synthetic_collector_version"
    assert report.get("collector_commit") == "synthetic_collector_version"

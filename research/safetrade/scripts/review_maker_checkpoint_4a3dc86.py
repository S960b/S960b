"""Independent offline checks for the published 6h checkpoint review.

Reuse the repository's isolated fixtures: process detection and kill are mocked.
No exchange calls, orders, real collector signals or raw run edits.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "checkpoint_review_fixtures", ROOT / "tests" / "test_econ_watchdog_review.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def test_crossed_book_does_not_refresh_valid_depth(tmp_path):
    now = time.time()
    helpers.fixture(tmp_path / "data" / "maker", [
        helpers.snapshot(now - 1000),
        helpers.snapshot(now - 1, bids=[["101", "1"]], asks=[["100", "1"]]),
    ], start=now - 2000)
    r = helpers.run_health(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "depth_age>120" in r.stdout, r.stdout


def test_completed_stale_data_still_gets_final_report(tmp_path):
    # The old regression had a freshly timestamped book after completion;
    # real completed collectors stop writing, so this book is already old.
    helpers.shell_fixture(tmp_path, alive=False, age=44 * 3600 + 300)
    for path in (tmp_path / "reports").iterdir():
        path.unlink()  # discard reports from fixture construction
    data = tmp_path / "data" / "maker"
    (data / f"{helpers.RID}_depth.jsonl").write_text(
        json.dumps(helpers.snapshot(time.time() - 300, "PRLUSDT")) + "\n")
    import os
    import subprocess
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "fakebin") + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["BASH_ENV"] = str(tmp_path / "bash_env")
    env["WATCHDOG_TEST_KILL_LOG"] = str(tmp_path / "mock_kill_calls")
    r = subprocess.run(["bash", str(tmp_path / "scripts" / helpers.WATCHDOG.name)],
                       env=env, text=True, capture_output=True, timeout=15)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "reports" / f"econ_{helpers.RID}_44h.json").exists()


def test_checkpoint_label_matches_exact_target_window(tmp_path):
    r, log = helpers.shell_fixture(tmp_path, age=2 * 3600 + 65)
    assert r.returncode == 0, (r.stderr, log)
    d = json.loads((tmp_path / "reports" / f"econ_{helpers.RID}_2h.json").read_text())
    m = json.loads((tmp_path / "data" / "maker" /
                    f"{helpers.RID}_manifest.json").read_text())
    assert d["cutoff_ns"] / 1e9 - m["started_epoch"] == pytest.approx(7200, abs=1e-6)


def test_unexpected_checker_failure_cannot_report_healthy(tmp_path):
    helpers.shell_fixture(tmp_path, age=600)
    # Valid JSON with an invalid row shape raises rc=1, not the manifest rc=2.
    (tmp_path / "data" / "maker" / f"{helpers.RID}_trades.jsonl").write_text("null\n")
    import os
    import subprocess
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "fakebin") + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["BASH_ENV"] = str(tmp_path / "bash_env")
    env["WATCHDOG_TEST_KILL_LOG"] = str(tmp_path / "mock_kill_calls")
    log_path = tmp_path / "logs" / "maker_watchdog.log"
    log_path.write_text("")
    r = subprocess.run(["bash", str(tmp_path / "scripts" / helpers.WATCHDOG.name)],
                       env=env, text=True, capture_output=True, timeout=15)
    log = log_path.read_text()
    assert r.stderr.strip(), "Fixture no longer produces an unexpected checker failure"
    assert " ok:" not in log, (r.returncode, log, r.stderr)
    assert r.returncode != 0 or any(x in log for x in ("ERROR", "WARN", "UNKNOWN"))


def test_exact_start_epoch_does_not_admit_a_pre_start_trade(tmp_path):
    start = helpers.START + .75
    helpers.fixture(tmp_path, [helpers.snapshot(start + 600)], [{
        "pair": helpers.PAIR, "t": int((start + 600) * 1e9),
        "ok": True, "coverage": "caught_up", "warmup": False,
        "trades": [{"id": 1, "created_at": helpers.iso(helpers.START + .25),
                    "total": "10", "side": "buy"}],
    }], start=start)
    p = tmp_path / f"{helpers.RID}_manifest.json"
    d = json.loads(p.read_text())
    # Collector's ISO field has second precision; started_epoch is exact.
    d["started_utc"] = helpers.iso(helpers.START)
    p.write_text(json.dumps(d))
    r = helpers.econ_report(str(tmp_path), helpers.RID,
                            cutoff_ns=int((start + 600) * 1e9))
    assert r["window_h_exact"] == pytest.approx(1 / 6, abs=1e-12)
    assert r["pairs"][0]["n_trades"] == 0

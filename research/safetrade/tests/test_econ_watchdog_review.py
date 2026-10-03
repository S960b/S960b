"""Offline regressions for econ/watchdog introduced in d891422.

Expected red on that commit. No network, orders or actual process signals.
The shell tests use an isolated project copy and mocked pgrep/kill.
Keep these tests in the repository rather than deleting an ad-hoc script.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maker.econ import econ_report

START = 1_791_014_400
PAIR = "PUSDT"
RID = "mk_econ_watchdog_review"
WATCHDOG = ROOT / "scripts" / "maker_collect_watchdog.sh"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot(epoch, pair=PAIR, bids=None, asks=None):
    return {"pair": pair, "t": int(epoch * 10**9), "ok": True,
            "bids": bids if bids is not None else [["99", "1"]],
            "asks": asks if asks is not None else [["100", "1"]]}


def fixture(root, depths, trades=None, start=START, pairs=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{RID}_manifest.json").write_text(json.dumps({
        "run_id": RID, "started_utc": iso(start), "started_epoch": start,
        "pairs": pairs or [PAIR], "params": {"minutes": 2640},
    }))
    for suffix, rows in (("depth", depths), ("trades", trades or [])):
        (root / f"{RID}_{suffix}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows))


def test_partial_purchase_cannot_be_a_full_budget_roundtrip(tmp_path):
    # The entire ask costs 1 USDT, so a 10-USDT entry is impossible.
    fixture(tmp_path, [snapshot(START + 600, asks=[["100", ".01"]])])
    r = econ_report(str(tmp_path), RID, cutoff_ns=(START + 600) * 10**9)
    stats = r["pairs"][0]["exit_by_budget"]["10"]
    assert stats["full_exit_pct"] == 0.0, stats


def test_default_window_ends_at_available_data_not_a_future_hour(tmp_path):
    polls = [{"pair": PAIR, "t": (START + 600) * 10**9,
              "ok": True, "warmup": False, "coverage": "caught_up",
              "trades": [{"id": 1, "created_at": iso(START + 300),
                          "price": "100", "amount": ".1", "total": "10", "side": "buy"}]}]
    fixture(tmp_path, [snapshot(START + 600)], polls)
    r = econ_report(str(tmp_path), RID)
    assert r["window_h"] == pytest.approx(1 / 6, abs=0.0005), r
    assert r["pairs"][0]["notional_per_hour"] == pytest.approx(60.0), r


def test_missing_future_observation_is_not_unchanged_price(tmp_path):
    fixture(tmp_path, [snapshot(START), snapshot(START + 20,
                                              bids=[["100", "1"]], asks=[["101", "1"]])])
    r = econ_report(str(tmp_path), RID, cutoff_ns=(START + 20) * 10**9)
    stats = r["pairs"][0]["touch_after"]["300"]
    assert stats["n_touch"] == 1
    assert stats["mid_unchanged"] == 0, stats
    # A corrected schema must also report this as unobserved/censored.


def check_code():
    return WATCHDOG.read_text().split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]


def run_health(root):
    return subprocess.run([sys.executable, "-", RID, str(root)], input=check_code(),
                          text=True, capture_output=True, timeout=10)


def test_fresh_pair_does_not_mask_another_stale_pair(tmp_path):
    now = time.time()
    fixture(tmp_path / "data" / "maker", [snapshot(now - 1000, "STALEUSDT"),
                                           snapshot(now - 1, "FRESHUSDT")],
            start=now - 2000, pairs=["STALEUSDT", "FRESHUSDT"])
    r = run_health(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "Stale pair was masked by a fresh pair"
    assert "STALEUSDT" in r.stdout, r.stdout


def test_http_success_with_empty_book_is_not_valid_fresh_depth(tmp_path):
    now = time.time()
    fixture(tmp_path / "data" / "maker", [snapshot(now - 1000),
                                           snapshot(now - 1, bids=[], asks=[])],
            start=now - 2000)
    r = run_health(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "HTTP ok=True hid stale valid depth"


def shell_fixture(tmp_path, *, alive=True, age=600, fail_analysis=False, partial_trades=False):
    # All filesystem writes are confined to this fixture. Process detection and
    # signaling are mocked; there is no real collector to stop.
    for rel in ("scripts", "maker", "data/maker", "venv/bin", "fakebin", "reports", "logs"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    source = WATCHDOG.read_text()
    assert "/bin/kill" not in source, "Adapt this fixture before introducing absolute signal commands"
    shutil.copyfile(WATCHDOG, tmp_path / "scripts" / WATCHDOG.name)
    for name in ("detail_analyze.py", "econ.py"):
        target = tmp_path / "maker" / name
        if fail_analysis:
            target.write_text("raise RuntimeError('intentional analysis failure')\n")
        else:
            shutil.copyfile(ROOT / "maker" / name, target)
    (tmp_path / "venv/bin/python").symlink_to(sys.executable)
    now = time.time()
    fixture(tmp_path / "data" / "maker", [snapshot(now, "PRLUSDT")],
            start=now - age, pairs=["PRLUSDT"])
    if not alive:
        (tmp_path / "data/maker" / f"{RID}_summary.json").write_text(
            json.dumps({"status": "completed"}))
    if partial_trades:
        (tmp_path / "data/maker" / f"{RID}_trades.jsonl").write_text('{"pair":')
    pgrep = tmp_path / "fakebin/pgrep"
    pgrep.write_text("#!/bin/sh\n" + ("echo 2147483647\n" if alive else "exit 1\n"))
    pgrep.chmod(0o755)
    kill = tmp_path / "fakebin/kill"
    kill.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$WATCHDOG_TEST_KILL_LOG"\n')
    kill.chmod(0o755)
    bash_env = tmp_path / "bash_env"
    bash_env.write_text('kill() { printf "%s\\n" "$*" >> "$WATCHDOG_TEST_KILL_LOG"; return 0; }\n')
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "fakebin") + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["BASH_ENV"] = str(bash_env)
    env["WATCHDOG_TEST_KILL_LOG"] = str(tmp_path / "mock_kill_calls")
    result = subprocess.run(["bash", str(tmp_path / "scripts" / WATCHDOG.name)],
                            env=env, text=True, capture_output=True, timeout=15)
    log = tmp_path / "logs/maker_watchdog.log"
    return result, log.read_text() if log.exists() else ""


def test_completed_collector_still_gets_final_44h_report(tmp_path):
    r, log = shell_fixture(tmp_path, alive=False, age=44 * 3600 + 60)
    assert r.returncode == 0, (r.stderr, log)
    assert (tmp_path / "reports" / f"econ_{RID}_44h.json").exists(), log
    assert (tmp_path / "reports" / f"{RID}_44h.done").exists(), log


def test_failed_analysis_does_not_get_a_done_marker(tmp_path):
    r, log = shell_fixture(tmp_path, age=2 * 3600 + 60, fail_analysis=True)
    assert not (tmp_path / "reports" / f"{RID}_2h.done").exists(), (r.returncode, log)


def test_checker_exception_is_not_reported_as_healthy(tmp_path):
    # Truncated final lines may be ignored carefully, but a failed checker must
    # never result in an unqualified healthy message from the shell wrapper.
    r, log = shell_fixture(tmp_path, partial_trades=True)
    if r.stderr.strip():
        assert " ok:" not in log, (r.returncode, log, r.stderr)
        assert r.returncode != 0 or any(x in log for x in ("ERROR", "WARN", "UNKNOWN")), log


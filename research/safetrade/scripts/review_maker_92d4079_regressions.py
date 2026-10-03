"""Additional offline regressions for 92d4079, expected red on that commit.

Run: python -m pytest -q scripts/review_maker_92d4079_regressions.py
No network, account access, orders, or changes to production data.
Preserve invariants if function interfaces are redesigned.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maker import detail_collect as collect
from maker.detail_analyze import analyze_run
from maker.feasibility import _fetch_trades
from scripts.review_maker_second_regressions import (
    START, END, PAIR, ImmediateLimiter, ProbeStop, depth, iso, poll, trade,
)


class DepthAdapter:
    async def rest_depth(self, *args, **kwargs):
        return {}, depth(START), START * 10**9, 2, START * 10**9, 1


def collector_fixture(monkeypatch):
    monkeypatch.setattr("adapters.get_adapter", lambda _: DepthAdapter())
    monkeypatch.setattr(collect, "RateLimiter", ImmediateLimiter)
    monkeypatch.setattr(collect, "TRADES_EVERY", 1)
    monkeypatch.setattr(collect, "TICK_S", 0)


def collect_until_stop(tmp_path):
    with pytest.raises(ProbeStop):
        asyncio.run(collect.run_detail([PAIR], 1, 20, str(tmp_path)))


def test_truncated_live_poll_does_not_advance_confirmed_boundary(monkeypatch, tmp_path):
    collector_fixture(monkeypatch)
    watermarks = []
    clock = [START]
    monkeypatch.setattr(collect.time, "time", lambda: clock[0])
    monkeypatch.setattr(collect.time, "time_ns", lambda: clock[0] * 10**9)

    async def fetch(adapter, native, limiter, watermark_time=None, max_pages=3):
        watermarks.append(watermark_time)
        if len(watermarks) == 1:
            clock[0] = START + 200
            return [trade(1, START + 100)], "full", 1, 1, START + 100, START + 100, 1
        if len(watermarks) == 2:
            clock[0] = START + 400
            # Trade 2 at start+200 was not fetched because a page failed/capped.
            return [trade(3, START + 300)], "history_truncated", 3, 3, START + 300, START + 300, 3
        raise ProbeStop("boundary captured")

    monkeypatch.setattr(collect, "_fetch_trades_detailed", fetch)
    collect_until_stop(tmp_path)
    assert len(watermarks) == 3
    assert watermarks[2] == START + 100, watermarks


def test_failed_trade_request_is_not_written_as_ok(monkeypatch, tmp_path):
    collector_fixture(monkeypatch)
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise ProbeStop("failed row captured")
        return [], "request_failed", None, None, None, None, 0

    monkeypatch.setattr(collect, "_fetch_trades_detailed", fetch)
    collect_until_stop(tmp_path)
    path = next((tmp_path / "data" / "maker").glob("*_trades.jsonl"))
    row = json.loads(path.read_text().splitlines()[0])
    assert row["coverage"] == "request_failed"
    assert row["ok"] is False, row
    summary = json.loads(next(path.parent.glob("*_summary.json")).read_text())
    assert summary["per_pair"][PAIR]["trades_age_s"] is None


def test_partial_page_overlap_does_not_stop_history_early():
    class Pages:
        def _http(self, url):
            page = int(url.rsplit("page=", 1)[1])
            ids = (range(201, 301) if page == 1 else range(102, 202) if page == 2
                   else range(2, 102) if page == 3 else [])
            return [trade(i, START + i) for i in ids]

    records, coverage, failed = asyncio.run(
        _fetch_trades(Pages(), "pusdt", ImmediateLimiter(), pages=4))
    assert failed is False
    assert len({row["id"] for row in records}) == 299, (len(records), coverage)


@pytest.mark.parametrize("ok,records", [
    (False, []), (True, [trade(1, END - 1)]),
], ids=["failed_empty_poll", "legacy_poll_without_coverage"])
def test_missing_coverage_is_not_proof_of_complete_observation(tmp_path, ok, records):
    rid = "mk_missing_coverage"
    row = poll(END, records, ok=ok)
    row.pop("coverage")
    (tmp_path / f"{rid}_manifest.json").write_text(json.dumps({"started_utc": iso(START)}))
    (tmp_path / f"{rid}_depth.jsonl").write_text(json.dumps(depth(END)) + "\n")
    (tmp_path / f"{rid}_trades.jsonl").write_text(json.dumps(row) + "\n")
    result = analyze_run(str(tmp_path), rid, cutoff_ns=END * 10**9)["pairs"][0]
    assert result["trade_coverage"] not in {"observed_window", "no_trades_observed", "full"}, result


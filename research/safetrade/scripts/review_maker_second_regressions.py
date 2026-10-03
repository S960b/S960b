"""Independent offline review of 0b71d28. These tests are EXPECTED TO FAIL there.

Run from research/safetrade:
    python -m pytest -q scripts/review_maker_second_regressions.py

All exchange calls are mocked. No keys, orders, network or live collector needed.
Assertions describe required behavior; do not change them to accept current bugs.
If an interface is redesigned, adapt the fixture while preserving the invariant.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maker import detail_collect as collect
from maker.detail_analyze import analyze_run
from maker.feasibility import _classify, _fetch_trades, screen_pair
from maker.util import valid_book

PAIR = "PUSDT"
START = 1_791_014_400  # 2026-10-03T08:00:00Z
END = START + 3600


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def trade(tid, epoch):
    return {"id": tid, "created_at": iso(epoch), "price": "100",
            "amount": ".01", "total": "1"}


def depth(epoch):
    return {"pair": PAIR, "t": epoch * 1_000_000_000, "ok": True,
            "bids": [["99", "10"]], "asks": [["100", "10"]]}


def poll(epoch, records, *, ok=True, coverage="caught_up"):
    return {"pair": PAIR, "t": epoch * 1_000_000_000, "ok": ok,
            "warmup": False, "coverage": coverage, "trades": records}


def analyze_fixture(tmp_path, polls, depths=None):
    rid = "mk_second_review_fixture"
    manifest = {"schema_version": 2, "run_id": rid, "started_utc": iso(START),
                "pairs": [PAIR]}
    (tmp_path / f"{rid}_manifest.json").write_text(json.dumps(manifest))
    for suffix, rows in (("depth", depths if depths is not None else [depth(END)]),
                         ("trades", polls)):
        (tmp_path / f"{rid}_{suffix}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
    result = analyze_run(str(tmp_path), rid, cutoff_ns=END * 1_000_000_000)
    return result["pairs"][0]


class ImmediateLimiter:
    def __init__(self, rpm=None):
        self.calls = 0
        self.blocks_429 = 0
        self.rtts = []

    async def wait(self):
        self.calls += 1

    def record_rtt(self, value):
        self.rtts.append(value)


class RepeatedPages:
    def _http(self, url):
        return [trade(i, START + 100 - i) for i in range(100)]


class EmptyTradesThinExit:
    def _http(self, url):
        return []

    async def rest_depth(self, *args, **kwargs):
        # Buy 0.1 base for 10 USDT; only 0.01 base is available to sell.
        snap = {"bids": [["99", ".01"]], "asks": [["100", "1"]]}
        return {}, snap, START * 1_000_000_000, 2, START * 1_000_000_000, 1


async def no_sleep(*args, **kwargs):
    return None


def thin_exit_screen(monkeypatch):
    monkeypatch.setattr("maker.feasibility.asyncio.sleep", no_sleep)
    return asyncio.run(screen_pair(
        EmptyTradesThinExit(), "pusdt", PAIR, "P",
        {"status": "enabled", "min_qty": ".001"}, ImmediateLimiter(),
        depth_snaps=1, trade_pages=1))


def test_screen_repeated_page_is_not_proof_of_full_history():
    records, coverage, failed = asyncio.run(
        _fetch_trades(RepeatedPages(), "pusdt", ImmediateLimiter(), pages=3))
    assert len(records) == 100
    assert not failed
    assert coverage not in {"full", "full_at_page", "caught_up"}, coverage


def test_detail_repeated_page_is_not_proof_of_full_history(monkeypatch):
    async def repeated(*args, **kwargs):
        return {"ok": True, "data": RepeatedPages()._http("")}
    monkeypatch.setattr(collect, "safe_get", repeated)
    records, coverage, *_ = asyncio.run(collect._fetch_trades_detailed(
        object(), "pusdt", ImmediateLimiter()))
    assert len(records) == 100
    assert coverage not in {"full", "full_at_page", "caught_up"}, coverage


def test_timestamp_ties_do_not_drop_unknown_trade_ids(monkeypatch):
    async def get_page(adapter, url, limiter):
        records = [trade(2, START + 100), trade(3, START + 100)] if url.endswith("page=1") else []
        return {"ok": True, "data": records}
    monkeypatch.setattr(collect, "safe_get", get_page)
    records, coverage, *_ = asyncio.run(collect._fetch_trades_detailed(
        object(), "pusdt", ImmediateLimiter(), watermark_time=START + 100))
    # An equal timestamp alone cannot prove that these IDs were seen previously.
    assert {row["id"] for row in records} == {2, 3}, (records, coverage)


def test_sell_exit_checks_filled_quantity(monkeypatch):
    result = thin_exit_screen(monkeypatch)
    assert result.depth_ask_10_cov == "full"
    assert result.bid_vwap_exit_10_cov == "partial", result.to_row()


def test_successful_empty_trades_cannot_pass(monkeypatch):
    result = thin_exit_screen(monkeypatch)
    _classify(result)
    assert result.trade_count == 0
    assert result.candidate_status != "pass", result.to_row()


@pytest.mark.parametrize("event_time", [START - 1, END + 1], ids=["before_start", "after_cutoff"])
def test_event_time_window_has_no_hidden_padding(tmp_path, event_time):
    result = analyze_fixture(tmp_path, [poll(END, [trade(1, event_time)])])
    assert result["n_trades_in_window"] == 0, result


def test_response_received_after_cutoff_cannot_leak_into_analysis(tmp_path):
    result = analyze_fixture(tmp_path, [poll(END + 1, [trade(1, END - 1)])])
    assert result["n_trades_in_window"] == 0, result


def test_depth_received_after_cutoff_is_excluded(tmp_path):
    result = analyze_fixture(tmp_path, [], [depth(START + 1), depth(END + 1)])
    assert result["n_valid_depth"] == 1, result


def test_one_trade_at_end_of_hour_is_one_trade_per_hour(tmp_path):
    result = analyze_fixture(tmp_path, [poll(END, [trade(1, END - 100)])])
    assert result["window_span_h"] == pytest.approx(1.0), result
    assert result["trades_per_hour"] == pytest.approx(1.0), result


def test_gap_between_trades_is_nonnegative_and_in_seconds(tmp_path):
    records = [trade(i, START + i * 300) for i in (1, 2, 3)]
    result = analyze_fixture(tmp_path, [poll(START + 1000, records)])
    assert result["gap_median_s"] == pytest.approx(300.0), result
    assert result["gap_p95_s"] == pytest.approx(300.0), result


def test_failed_poll_cannot_be_reported_as_no_trades(tmp_path):
    result = analyze_fixture(tmp_path, [poll(END, [], ok=False, coverage="request_failed")])
    assert result["trade_coverage"] not in {"no_trades_observed", "observed_window", "full"}, result


def test_truncated_poll_keeps_coverage_warning(tmp_path):
    result = analyze_fixture(tmp_path, [poll(END, [trade(1, END - 1)], coverage="history_truncated")])
    # Observed trades remain useful, but an unqualified normal coverage is false.
    assert result["n_trades_in_window"] == 1
    assert result["trade_coverage"] not in {"observed_window", "full", "caught_up"}, result


class ProbeStop(RuntimeError):
    pass


class DepthAdapter:
    async def rest_depth(self, *args, **kwargs):
        return {}, depth(START), START * 1_000_000_000, 2, START * 1_000_000_000, 1


def setup_collector(monkeypatch, adapter=None):
    monkeypatch.setattr("adapters.get_adapter", lambda name: adapter or DepthAdapter())
    monkeypatch.setattr(collect, "RateLimiter", ImmediateLimiter)
    monkeypatch.setattr(collect, "TRADES_EVERY", 1)
    monkeypatch.setattr(collect, "TICK_S", 0)


def run_until_probe_stop(tmp_path):
    try:
        asyncio.run(collect.run_detail([PAIR], minutes=1, rpm=20, base_dir=str(tmp_path)))
    except ProbeStop:
        pass


def test_unexpected_collector_error_propagates(monkeypatch, tmp_path):
    setup_collector(monkeypatch)

    async def fail(*args, **kwargs):
        raise ProbeStop("synthetic collector failure")

    monkeypatch.setattr(collect, "_fetch_trades_detailed", fail)
    with pytest.raises(ProbeStop, match="synthetic collector failure"):
        asyncio.run(collect.run_detail([PAIR], minutes=1, rpm=20, base_dir=str(tmp_path)))


def test_depth_request_uses_shared_limiter(monkeypatch, tmp_path):
    limiters = []
    calls_at_depth = []

    class SpyLimiter(ImmediateLimiter):
        def __init__(self, rpm):
            super().__init__(rpm)
            limiters.append(self)

    class SpyAdapter(DepthAdapter):
        async def rest_depth(self, *args, **kwargs):
            calls_at_depth.append(limiters[0].calls)
            return await super().rest_depth(*args, **kwargs)

    setup_collector(monkeypatch, SpyAdapter())
    monkeypatch.setattr(collect, "RateLimiter", SpyLimiter)

    async def stop(*args, **kwargs):
        raise ProbeStop("stop after first depth")

    monkeypatch.setattr(collect, "_fetch_trades_detailed", stop)
    run_until_probe_stop(tmp_path)
    assert calls_at_depth and calls_at_depth[0] >= 1, calls_at_depth


def test_next_poll_uses_newest_confirmed_boundary_not_oldest_tail(monkeypatch, tmp_path):
    setup_collector(monkeypatch)
    observed = []

    async def fetch(adapter, native, limiter, watermark_time=None):
        observed.append(watermark_time)
        if len(observed) > 1:
            raise ProbeStop("stop after boundary observation")
        records = [trade(2, START + 100), trade(1, START + 90)]
        return records, "full", 1, 2, START + 100, START + 90, 1

    monkeypatch.setattr(collect, "_fetch_trades_detailed", fetch)
    run_until_probe_stop(tmp_path)
    assert len(observed) == 2, observed
    assert observed[1] == START + 100, observed


@pytest.mark.parametrize("bids,asks", [
    ([["99", "inf"]], [["100", "1"]]),
    ([["99", "1"]], [["inf", "1"]]),
    ([["99", "0"]], [["100", "0"]]),
], ids=["infinite_qty", "infinite_price", "zero_qty_book"])
def test_book_has_finite_positive_active_levels(bids, asks):
    assert valid_book(bids, asks)[0] is False

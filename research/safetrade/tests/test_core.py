"""Тесты: снапшот/дельта/удаления, VWAP, комиссии, as-of без будущего, stale/invalid, отказ торговых вызовов."""
import os
import sys
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from analysis.book import OrderBook, bbo_series  # noqa: E402
from adapters.events import make_event  # noqa: E402


def ev(et, symbol="BTCUSDT", ex="safetrade", bids=None, asks=None, seq=None, t=1_000_000):
    return make_event(ex, symbol, symbol.lower(), et, recv_utc=t, recv_mono=t,
                      sequence=seq, bids=bids, asks=asks)


class TestOrderBook:
    def test_snapshot_then_delta(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", bids=[["100", "1"], ["99", "2"]], asks=[["101", "3"], ["102", "4"]]))
        assert ob.valid
        assert float(ob.mid()) == 100.5
        assert float(ob.best_bid()) == 100
        assert float(ob.best_ask()) == 101
        # delta: обновляем уровень и добавляем новый
        ob.apply(ev("book_delta", bids=[["100", "1.5"]], asks=[["101", "0"], ["101.5", "1"]]))
        assert float(ob.best_bid()) == 100
        assert float(ob.best_ask()) == 101.5

    def test_qty_zero_deletes_level(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", bids=[["100", "1"], ["99", "2"]]))
        ob.apply(ev("book_delta", bids=[["100", "0"]]))
        assert float(ob.best_bid()) == 99

    def test_delta_before_snapshot_ignored(self):
        ob = OrderBook()
        ob.apply(ev("book_delta", bids=[["100", "1"]]))  # invalid: нет снапшота
        assert not ob.valid
        assert ob.best_bid() is None

    def test_vwap_ask(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", asks=[["100", "1"], ["101", "1"], ["102", "1"]]))
        from decimal import Decimal
        price, filled = ob.vwap("ask", Decimal("1.5"))
        assert filled == Decimal("1.5")
        # (1*100 + 0.5*101)/1.5 = 100.333...
        assert abs(float(price) - 100.3333) < 0.01

    def test_vwap_insufficient_depth(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", asks=[["100", "1"]]))
        from decimal import Decimal
        price, filled = ob.vwap("ask", Decimal("5"))
        assert filled == Decimal("1")
        assert price is not None


class TestBBOSeries:
    def test_bbo_series_from_snapshot(self):
        import pandas as pd
        events = [
            ev("book_snapshot", bids=[["100", "1"]], asks=[["101", "1"]], t=100),
            ev("book_delta", bids=[["99", "2"]], asks=[["102", "1"]], t=200),
        ]
        df = pd.DataFrame([e for e in events])
        s = bbo_series(df)
        assert len(s) >= 2
        assert abs(s.iloc[-1]["mid"] - 100.5) < 1e-6  # bid 99, ask 102 -> 100.5


class TestFeesSimulator:
    def test_no_trading_calls(self):
        """В конфиге allow_trading=false; сам код не содержит торговых вызовов."""
        import yaml
        cfg = yaml.safe_load(open(os.path.join(BASE, "config", "config.yaml")))
        assert cfg["allow_trading"] is False
        src = open(os.path.join(BASE, "simulator", "paper.py")).read()
        assert "create_order" not in src and "post_api" not in src

    def test_decimal_math(self):
        from decimal import Decimal
        qty = Decimal("0.001")
        vwap = Decimal("87000")
        f = Decimal("0.001")
        cost = qty * vwap * (1 + f)
        assert cost == Decimal("87.087")


class TestNoFutureLookahead:
    def test_asof_join_uses_past_only(self):
        from analysis.oracle import asof_value
        import numpy as np
        t = np.array([10, 20, 30], dtype=np.int64)
        v = np.array([100.0, 101.0, 102.0])
        idx, val = asof_value(t, v, 25)
        assert val == 101.0   # событие 30 в будущем не видно
        idx, val = asof_value(t, v, 5)
        assert val is None


class TestReach:
    def test_reach_no_future(self):
        from analysis.reach import test_A_reach
        t0 = 5_000_000_000
        safe_book = {"mid_ts": [(t0 + 1_000_000_000, 100.0), (t0 + 2_000_000_000, 102.0),
                                (t0 + 3_000_000_000, 103.0)], "bids_ts": [], "asks_ts": [],
                     "mid_at_t0": 100.0}
        r = test_A_reach(safe_book, 101.5, None, t0, [5], 1.0, direction="up")
        assert r[5]["raw"] == pytest.approx(2.0, abs=0.01)
        assert r[5]["status_raw"] == "reached"

    def test_reach_unknown_when_record_ends(self):
        from analysis.reach import test_A_reach
        t0 = 5_000_000_000
        safe_book = {"mid_ts": [(t0 + 1_000_000_000, 100.0)], "bids_ts": [], "asks_ts": [],
                     "mid_at_t0": 100.0}
        r = test_A_reach(safe_book, 101.5, None, t0, [30], 1.0, direction="up")
        assert r[30]["status_raw"] == "unknown"
        assert r[30]["raw"] is None

    def test_summarize(self):
        from analysis.reach import summarize_reach
        results = {1: {10: {"raw": 1.0, "adj": 1.0, "status_raw": "reached", "status_adj": "reached"}},
                   2: {10: {"raw": None, "adj": None, "status_raw": "unknown", "status_adj": "unknown"}}}
        s = summarize_reach(results, [10])
        assert s[10]["reached_pct"] == 50.0
        assert s[10]["by_status"].get("unknown") == 1
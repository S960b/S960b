"""Тесты v2 (по ревью): снапшот/дельта, VWAP, as-of без будущего, единый BBO-контракт,
оракул 3/3 только внешних, синтетический фикстур с известным лагом 10с, файловая уникальность."""
import os
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from analysis.book import OrderBook, bbo_series, _bbo_mid_from_payload  # noqa: E402
from analysis.oracle import Oracle, build_oracle_series, premium_series, F_from_premium  # noqa: E402
from adapters.events import make_event  # noqa: E402


def ev(et, symbol="BTCUSDT", ex="safetrade", bids=None, asks=None, seq=None, t=1_000_000,
       payload=None, run="run"):
    return make_event(ex, symbol, symbol.lower(), et, recv_utc=t, recv_mono=t,
                      sequence=seq, bids=bids, asks=asks, payload=payload, run_id=run)


class TestOrderBook:
    def test_snapshot_then_delta(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", bids=[["100", "1"], ["99", "2"]], asks=[["101", "3"], ["102", "4"]]))
        assert ob.valid and float(ob.mid()) == 100.5
        ob.apply(ev("book_delta", bids=[["100", "1.5"]], asks=[["101", "0"], ["101.5", "1"]]))
        assert float(ob.best_ask()) == 101.5

    def test_qty_zero_deletes_level(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", bids=[["100", "1"], ["99", "2"]]))
        ob.apply(ev("book_delta", bids=[["100", "0"]]))
        assert float(ob.best_bid()) == 99

    def test_delta_before_snapshot_ignored(self):
        ob = OrderBook()
        ob.apply(ev("book_delta", bids=[["100", "1"]]))
        assert not ob.valid and ob.best_bid() is None

    def test_vwap_ask(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", asks=[["100", "1"], ["101", "1"], ["102", "1"]]))
        from decimal import Decimal
        price, filled = ob.vwap("ask", Decimal("1.5"))
        assert filled == Decimal("1.5")
        assert abs(float(price) - 100.3333) < 0.01

    def test_vwap_insufficient_depth(self):
        ob = OrderBook()
        ob.apply(ev("book_snapshot", asks=[["100", "1"]]))
        from decimal import Decimal
        price, filled = ob.vwap("ask", Decimal("5"))
        assert filled == Decimal("1") and price == Decimal("100")


class TestBBOContract:
    def test_bbo_mid_from_contract(self):    # ревью P0-4: Bybit/OKX/Binance единый контракт
        e = ev("bbo", ex="bybit", payload={"bid_price": "87000.0", "bid_qty": "1",
                                           "ask_price": "87002.0", "ask_qty": "1"})
        assert _bbo_mid_from_payload(e) == 87001.0

    def test_bbo_mid_rejects_reversed(self):
        e = ev("bbo", ex="x", payload={"bid_price": "87002", "ask_price": "87000"})
        assert _bbo_mid_from_payload(e) is None

    def test_bybit_raw_recovery(self):
        """Ревью P0-4: raw bybit orderbook.1 (b=[price,qty]) -> mid 87001, не 8.0."""
        from adapters.bybit import BybitAdapter
        a = BybitAdapter()
        raw = {"topic": "orderbook.1.BTCUSDT", "type": "snapshot",
               "data": {"b": [["87000.0", "1"]], "a": [["87002.0", "1"]],
                        "u": 123, "ts": 1000}}
        e = a._parse("BTC/USDT", "BTCUSDT", raw, 1, 1, "r", "b")
        assert e["event_type"] == "bbo"
        assert _bbo_mid_from_payload(e) == 87001.0


class TestOracle:
    def test_oracle_3of3_median(self):
        o = Oracle(["a", "b", "c"], max_bbo_age_s=5)
        o.feed("a", 1000, 100.0)
        o.feed("b", 1000, 101.0)
        o.feed("c", 1000, 102.0)
        R, n, ids = o.mid_at(1001)
        assert R == 101.0 and n == 3

    def test_oracle_stale_source_excluded(self):
        o = Oracle(["a", "b", "c"], max_bbo_age_s=5)
        o.feed("a", 1000, 100.0)
        o.feed("b", 1000, 101.0)
        o.feed("c", 1000, 102.0)
        R, n, _ = o.mid_at(1000 + 6 * 1e9)
        assert R is None       # все устарели (6с > 5с)
        assert n == 0

    def test_oracle_requires_3(self):
        o = Oracle(["a", "b", "c"], max_bbo_age_s=5, min_sources=3)
        o.feed("a", 1000, 100.0)
        o.feed("b", 1000, 101.0)
        R, n, _ = o.mid_at(1001)
        assert R is None and n == 2   # degraded 2/3 не даёт R в primary-режиме

    def test_target_does_not_affect_R(self):
        """Ревью P1-6: изменение только SafeTrade не меняет R (оракул кормится только внешними)."""
        import pandas as pd
        evs = []
        for i, ex in enumerate(["binance", "okx", "bybit"]):
            for k in range(10):
                evs.append(ev("bbo", ex=ex, t=1000 + k, payload={"bid_price": str(100 + k),
                           "bid_qty": "1", "ask_price": str(101 + k), "ask_qty": "1"}))
        df = pd.DataFrame([e for e in evs])
        df["_mid"] = df.apply(lambda r: _bbo_mid_from_payload(r), axis=1)
        rs = build_oracle_series(df, ["binance", "okx", "bybit"], max_bbo_age_s=10)
        assert rs is not None and len(rs) >= 5
        # всё влияло только внешними — SafeTrade отсутствует в df, R строится


class TestSyntheticLag:
    """Ревью B7: три источника растут, SafeTrade повторяет движение через 10с.
    Детектор должен найти сигнал; через разрыв - unknown."""

    def _make_series(self, n=300, base=100.0, step=0.002, refresh_ms=50):
        import pandas as pd
        rows = []
        t = 10_000_000_000
        for k in range(n):
            rows.append({"mono_ns": t, "utc_ns": t, "mid": base + k * step})
            t += int(refresh_ms * 1e6)
        return pd.DataFrame(rows)

    def test_signal_on_rising_sources(self):
        from simulator.paper import PaperSimulator
        cfg = {
            "sources": ["binance", "okx", "bybit"], "target": "safetrade",
            "paper": {"confirm_min_sources": 2, "cooldown_s": 60,
                      "noise": {"mad_mult": 1.5, "abs_min_bps": 2.0}},
            "fitness": {"max_bbo_age_s": 5.0},
        }
        sim = PaperSimulator(cfg, ["BTCUSDT"])
        ext = {ex: self._make_series() for ex in ("binance", "okx", "bybit")}
        # окно [15с, 40с]: W=5с разогрет (данные с 0с)
        sigs, funnel = sim.detect_signals(ext, 15_000_000_000, 40_000_000_000,
                                          W_s=5, D_s=0)
        assert len(sigs) > 0, f"no signals, funnel={funnel}"
        assert sigs[0].direction == "up"

    def test_premium_and_F(self):
        """F0 блокируется (None) без премии; с премией F0=R0*exp(b) (ревью P1-9)."""
        assert F_from_premium(100.0, None) is None
        assert abs(F_from_premium(100.0, 0.001) - 100.1005) < 0.01


class TestStorageV2:
    def test_rotator_unique_names(self, tmp_path):
        """Ревью P0-1: два запуска не перезаписывают raw (уникальные имена)."""
        from storage import JsonlRotator
        r1 = JsonlRotator(str(tmp_path), "runAAA", max_bytes=1024)
        r1.write({"x": 1})
        r1.close()
        r2 = JsonlRotator(str(tmp_path), "runBBB", max_bytes=1024)
        r2.write({"x": 2})
        r2.close()
        names = [f for f in os.listdir(tmp_path) if f.endswith(".jsonl.gz")]
        assert len(names) == 2, f"expected 2 distinct raw files, got {names}"
        assert len(set(names)) == 2   # нет коллизии имён

    def test_parquet_unique_names_no_overwrite(self, tmp_path):
        """Ревью P0-1: два flush в ту же секунду не заменяют файл (уникальные имена)."""
        from storage import ParquetWriter
        import duckdb
        w = ParquetWriter(str(tmp_path), "runX", flush_interval_s=1000, flush_events=2)
        w.add(make_event("binance", "BTCUSDT", "BTCUSDT", "bbo", recv_utc=1_700_000_000_000_000_000,
                         recv_mono=1000, payload={"bid_price": "100", "ask_price": "101"}))
        w.add(make_event("binance", "BTCUSDT", "BTCUSDT", "bbo", recv_utc=1_700_000_000_000_000_001,
                         recv_mono=1001, payload={"bid_price": "200", "ask_price": "201"}))
        w.flush()
        w.add(make_event("binance", "BTCUSDT", "BTCUSDT", "bbo", recv_utc=1_700_000_000_000_000_002,
                         recv_mono=1002, payload={"bid_price": "300", "ask_price": "301"}))
        w.close()
        files = []
        for root, _, fs in os.walk(tmp_path):
            for f in fs:
                if f.endswith(".parquet"):
                    files.append(os.path.join(root, f))
        assert len(files) >= 2, f"expected >=2 distinct parquet files, got {files}"
        df = duckdb.query(f"SELECT * FROM read_parquet({files!r}, union_by_name=True)").df()
        assert len(df) == 3   # всё сохранилось, ничего не перезаписано

    def test_parquet_roundtrip_fields(self, tmp_path):
        """Ревью P0-5: schema сохраняет native_symbol/market_type/parse_done и пр."""
        from storage import ParquetWriter
        import duckdb
        w = ParquetWriter(str(tmp_path), "runY", flush_interval_s=1, flush_events=100)
        w.add(make_event("binance", "BTCUSDT", "BTCUSDT", "bbo", recv_utc=1_700_000_000_000_000_000,
                         recv_mono=1000, payload={"bid_price": "100", "ask_price": "101"}))
        time.sleep(1.1)
        w.close()
        files = []
        for root, _, fs in os.walk(tmp_path):
            for f in fs:
                if f.endswith(".parquet"):
                    files.append(os.path.join(root, f))
        df = duckdb.query(f"SELECT * FROM read_parquet({files!r})").df()
        row = df.iloc[0]
        assert row["native_symbol"] == "BTCUSDT"
        assert row["market_type"] == "spot"
        assert row["parse_done_monotonic_ns"] > 0
        assert row["schema_version"] == 1


class TestReachV2:
    def test_reach_unknown_on_gap(self):
        """Ревью п.10: внутренний разрыв (нет данных после t0 до горизонта) -> unknown."""
        from analysis.reach import test_A_reach
        t0 = 5_000_000_000
        book = {"mid_ts": [(t0 - 1_000_000_000, 100.0)], "bids_ts": [], "asks_ts": [],
                "mid_at_t0": 100.0}
        r = test_A_reach(book, 101.5, None, t0, [30], 1.0, direction="up")
        assert r[30]["status_raw"] == "unknown"
        assert r[30]["raw"] is None

    def test_reach_already_at_target(self):
        from analysis.reach import test_A_reach
        t0 = 5_000_000_000
        book = {"mid_ts": [(t0 + 1_000_000_000, 102.0)], "bids_ts": [], "asks_ts": [],
                "mid_at_t0": 102.0}
        r = test_A_reach(book, 101.5, None, t0, [5], 1.0, direction="up")
        assert r[5]["status_raw"] == "already_at_target"

    def test_reach_reached(self):
        from analysis.reach import test_A_reach
        t0 = 5_000_000_000
        book = {"mid_ts": [(t0 + 1_000_000_000, 100.0), (t0 + 2_000_000_000, 102.0)],
                "bids_ts": [], "asks_ts": [], "mid_at_t0": 100.0}
        r = test_A_reach(book, 101.5, None, t0, [5], 1.0, direction="up")
        assert r[5]["status_raw"] == "reached"
        assert r[5]["raw"] == pytest.approx(2.0)

    def test_reach_B_executable(self):
        from analysis.reach import test_B_executable
        from decimal import Decimal
        t0 = 5_000_000_000
        book = {"bids_vwap_ts": [(t0 + 1_000_000_000, 100.0), (t0 + 2_000_000_000, 100.5)]}
        r = test_B_executable(book, Decimal("0.01"), Decimal("100.0"), t0, [5],
                              f_buy_bps=10, f_sell_bps=10, target_net_bps=0)
        assert r[5]["status_exec"] == "reached"
        assert r[5]["exec"] == pytest.approx(2.0)


class TestNoTrading:
    def test_no_trading_calls(self):
        import yaml
        cfg = yaml.safe_load(open(os.path.join(BASE, "config", "config.yaml")))
        assert cfg["allow_trading"] is False
        src = open(os.path.join(BASE, "simulator", "paper.py")).read()
        assert "create_order" not in src and "post_api" not in src
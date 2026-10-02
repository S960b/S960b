"""Smoke-тесты адаптеров и storage (без сети — только структура/нормализация)."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


class TestAdaptersStructure:
    def test_all_adapters_registered(self):
        from adapters import get_adapter, ADAPTERS
        for name in ("binance", "okx", "bybit", "safetrade"):
            assert name in ADAPTERS
            a = get_adapter(name)
            assert hasattr(a, "discover_markets")
            assert hasattr(a, "stream")
            assert hasattr(a, "health")

    def test_event_schema_fields(self):
        from adapters.events import make_event
        e = make_event("test", "BTCUSDT", "BTCUSDT", "bbo", price="100", qty="1")
        for f in ("schema_version", "run_id", "boot_id", "exchange", "canonical_symbol",
                  "native_symbol", "market_type", "event_type", "exchange_event_ts",
                  "recv_utc_ns", "recv_monotonic_ns", "parse_done_monotonic_ns",
                  "sequence", "side", "price", "qty", "bids", "asks", "quality_flags"):
            assert f in e
        assert e["schema_version"] == 1

    def test_recv_before_parse_timestamps(self):
        """recv_utc фиксируется до parse_done: recv <= parse_done (монотонно)."""
        from adapters.events import make_event
        e = make_event("test", "BTCUSDT", "BTCUSDT", "bbo")
        assert e["recv_monotonic_ns"] <= e["parse_done_monotonic_ns"]


class TestStorage:
    def test_jsonl_rotator_write(self, tmp_path):
        from storage import JsonlRotator
        r = JsonlRotator(str(tmp_path), "runT", max_bytes=1024)
        ref = r.write({"a": 1})
        assert ref  # raw_ref не пустой
        r.close()
        written = [f for f in os.listdir(tmp_path) if f.endswith((".jsonl", ".jsonl.gz"))]
        assert len(written) >= 1

    def test_parquet_writer_roundtrip(self, tmp_path):
        from storage import ParquetWriter
        from adapters.events import make_event
        import duckdb
        w = ParquetWriter(str(tmp_path), "runT", flush_interval_s=1000, flush_events=100)
        for i in range(5):
            e = make_event("binance", "BTCUSDT", "BTCUSDT", "bbo", price=f"{100+i}", qty="1",
                           recv_utc=1_700_000_000_000_000_000 + i, recv_mono=1000 + i,
                           payload={"bid_price": f"{100+i}", "ask_price": f"{101+i}"})
            e["bids"] = None
            w.add(e)
        w.close()
        files = []
        for root, _, fs in os.walk(tmp_path):
            for f in fs:
                if f.endswith(".parquet"):
                    files.append(os.path.join(root, f))
        assert files
        df = duckdb.query(f"SELECT * FROM read_parquet({files!r})").df()
        assert len(df) == 5
        assert set(df.exchange) == {"binance"} | set(df.exchange)

    def test_state_store(self, tmp_path):
        from storage import StateStore
        s = StateStore(str(tmp_path / "state.db"))
        s.start_run("run1", "boot1", ["binance"], ["BTC/USDT"])
        s.save_health("binance", "BTCUSDT", {"connected": 1, "last_msg_age_s": 0.1,
                                             "msgs": 10, "reconnects": 0, "errors": []})
        s.set_ui("disk_free", 12345)
        assert s.get_ui("disk_free") == 12345
        s.stop_run("run1")
        s.close()
"""R6 regression tests for PID symlink and validator error provenance."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "source" / "v22_live"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import collector_only as CO
import tier2_label_watch as LW
import tier2 as T2


class R6Regressions(unittest.TestCase):
    def test_pid_symlink_never_overwrites_victim(self):
        with tempfile.TemporaryDirectory() as d:
            victim = Path(d) / "victim"
            victim.write_text("DO_NOT_TOUCH")
            pid = Path(d) / "collector.pid"
            pid.symlink_to(victim)
            with self.assertRaises((CO.SingletonRefused, OSError)):
                CO.claim_singleton(pid)
            self.assertEqual(victim.read_text(), "DO_NOT_TOUCH")
            self.assertTrue(pid.is_symlink())

    def test_local_validation_not_infinite_pending(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        due = 1_800_000_000_000
        row = {"event_id": "fixture", "due_ms": due}
        old = T2.validate_candle
        try:
            def broken(*a, **kw):
                raise T2.Tier2Error("candle_not_closed")
            T2.validate_candle = broken
            result = LW.settle_one(con, row, now_ms=due + 61_000,
                                   candle_fn=lambda **kw: {"close": 100},
                                   dry=False, max_failures=3)
            self.assertNotEqual(result.get("reason"),
                                "await_exchange_candle_close")
            self.assertIn(result["status"], ("pending", "censored", "error"))
            if result["status"] == "pending":
                self.assertGreater(result.get("attempts", 0), 0)
        finally:
            T2.validate_candle = old
            con.close()


if __name__ == "__main__":
    unittest.main()

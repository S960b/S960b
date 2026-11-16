"""NEWS v2.1 — per-pair spend circuit breaker and usage reconciliation."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import v21_cycle as C
import v21_usage as U


class FakeGateway:
    """A stand-in gateway DB with message rows shaped like the real one."""

    def __init__(self, completions, tokens_each, cost_each):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(self.path)
        con.executescript("""
          CREATE TABLE session(id TEXT PRIMARY KEY, title TEXT, time_created INT);
          CREATE TABLE message(session_id TEXT, data TEXT);
        """)
        con.execute("INSERT INTO session VALUES(?,?,?)",
                    ("s1", "NEWS v2.1 news abcdef123456", 1))
        con.execute("INSERT INTO session VALUES(?,?,?)",
                    ("s2", "NEWS v2.1 news_market abcdef123456", 2))
        for _ in range(completions):
            for sid in ("s1", "s2"):
                con.execute("INSERT INTO message VALUES(?,?)",
                            (sid, json.dumps({
                                "role": "assistant", "providerID": "deepseek",
                                "modelID": "deepseek-v4-flash",
                                "tokens": {"input": tokens_each},
                                "cost": cost_each})))
        con.commit()
        con.close()

    def cleanup(self):
        os.unlink(self.path)


class CircuitBreakerTests(unittest.TestCase):
    def _cost(self, completions, tokens, cost):
        gw = FakeGateway(completions, tokens, cost)
        try:
            with mock.patch.object(C.Path, "home",
                                   return_value=Path(gw.path).parent):
                # redirect the hardcoded gateway path at the fixture
                fake = Path(gw.path).parent / ".local/share/gigatool/opencode.db"
                fake.parent.mkdir(parents=True, exist_ok=True)
                if not fake.exists():
                    os.link(gw.path, fake)
                return C._pair_cost(None, "abcdef123456")
        finally:
            gw.cleanup()

    def test_usage_is_read_back(self):
        usage = self._cost(2, 1000, 0.001)
        self.assertEqual(usage["completions"], 4)
        self.assertEqual(usage["input_tokens"], 4000)

    def test_limits_are_sane_numbers(self):
        self.assertLessEqual(C.MAX_COMPLETIONS_PER_PAIR, 16)
        self.assertGreater(C.MAX_COMPLETIONS_PER_PAIR, 2)

    def test_there_is_no_monetary_stop(self):
        # D13: the money ceiling was removed by owner decision - inference is
        # free for this project and a USD gate would silently stop the
        # experiment. Only the loop guard may remain.
        self.assertFalse(hasattr(C, "MAX_COST_USD_PER_PAIR"))
        self.assertFalse(hasattr(C, "MAX_INPUT_TOKENS_PER_PAIR"))


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(self.db)
        con.executescript("""
          CREATE TABLE session(id TEXT PRIMARY KEY, title TEXT, time_created INT);
          CREATE TABLE message(session_id TEXT, data TEXT);
          CREATE TABLE part(session_id TEXT, data TEXT);
        """)
        con.execute("INSERT INTO session VALUES('s1','NEWS v2.1 probe',1)")
        for _ in range(3):
            con.execute("INSERT INTO message VALUES('s1',?)", (json.dumps({
                "role": "assistant", "providerID": "deepseek",
                "modelID": "deepseek-v4-flash",
                "tokens": {"input": 100, "output": 10}, "cost": 0.001}),))
        con.execute("INSERT INTO part VALUES('s1',?)",
                    (json.dumps({"type": "tool", "tool": "websearch"}),))
        con.execute("INSERT INTO part VALUES('s1',?)",
                    (json.dumps({"type": "text", "text": "hi"}),))
        con.commit()
        con.close()
        self.con = U.connect(self.db)

    def tearDown(self):
        self.con.close()
        os.unlink(self.db)

    def test_three_numbers_are_kept_apart(self):
        sessions = U.session_usage(self.con)
        total = U.summarise(sessions)["total"]
        self.assertEqual(total["compl"], 3, "completions, not reservations")
        self.assertEqual(total["input"], 300)
        self.assertEqual(total["output"], 30)
        self.assertEqual(total["tools"], 1)
        self.assertAlmostEqual(total["cost"], 0.003, places=6)

    def test_missing_gateway_db_is_reported_not_guessed(self):
        fd, missing = tempfile.mkstemp()
        os.close(fd)
        os.unlink(missing)
        rc = U.main(["--gateway-db", missing])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

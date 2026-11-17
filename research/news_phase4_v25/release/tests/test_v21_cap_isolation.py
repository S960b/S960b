"""NEWS v2.1 — shared-cap fail-closed and fixture/production isolation."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import v21_dispatch as D
import v21_paper_prep as P

NOW = 1_800_000_000_000
HOUR = 3_600_000


class CapTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.con = D.open_db(self.db, create=True)

    def tearDown(self):
        self.con.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db + suf)
            except FileNotFoundError:
                pass

    def test_missing_legacy_db_fails_closed(self):
        missing = Path("/tmp/gigatool/definitely-not-here/v2.sqlite")
        with mock.patch.object(D, "LEGACY_V2_DB", missing):
            self.assertEqual(D._legacy_calls_today(NOW), D.MAX_CALLS_PER_DAY)

    def test_tiny_or_corrupt_legacy_db_fails_closed(self):
        fd, tiny = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            with mock.patch.object(D, "LEGACY_V2_DB", Path(tiny)):
                self.assertEqual(D._legacy_calls_today(NOW),
                                 D.MAX_CALLS_PER_DAY)
        finally:
            os.unlink(tiny)

    def test_unreadable_legacy_table_fails_closed(self):
        fd, other = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            con = sqlite3.connect(other)
            con.execute("CREATE TABLE nonsense(x)")
            con.commit()
            con.close()
            self.assertGreater(os.path.getsize(other), 4096 - 1)
            with mock.patch.object(D, "LEGACY_V2_DB", Path(other)):
                self.assertEqual(D._legacy_calls_today(NOW),
                                 D.MAX_CALLS_PER_DAY)
        finally:
            os.unlink(other)

    def test_legacy_calls_are_counted(self):
        fd, legacy = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            con = sqlite3.connect(legacy)
            con.execute("CREATE TABLE attempts(id TEXT, reserved_ms INTEGER)")
            for i in range(3):
                con.execute("INSERT INTO attempts VALUES(?,?)",
                            (f"a{i}", NOW - 1000))
            con.commit()
            con.close()
            with mock.patch.object(D, "LEGACY_V2_DB", Path(legacy)):
                self.assertEqual(D._legacy_calls_today(NOW), 3)
        finally:
            os.unlink(legacy)

    def test_production_is_blocked_when_legacy_used_the_budget(self):
        with mock.patch.object(D, "_legacy_calls_today",
                               lambda _now: D.MAX_CALLS_PER_DAY):
            ev = {"event_id": "a" * 64, "source": "x",
                  "cohort": "context_news", "published_ms": NOW - 60_000,
                  "observed_ms": NOW - 50_000, "title": "t", "summary": "s"}
            D.enqueue(self.con, ev, synthetic=False, now_ms=NOW)
            r = D.reserve_pair(self.con, ev["event_id"], synthetic=False,
                               now_ms=NOW)
            self.assertEqual(r["status"], "skip")
            self.assertEqual(r["reason"], "daily_call_cap")


class IsolationTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.con = D.open_db(self.db, create=True)
        self.ev = {"event_id": "b" * 64, "source": "x",
                   "cohort": "context_news", "published_ms": NOW - 60_000,
                   "observed_ms": NOW - 50_000, "title": "t", "summary": "s"}

    def tearDown(self):
        self.con.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db + suf)
            except FileNotFoundError:
                pass

    def test_fixture_attempt_ids_differ_from_production(self):
        D.enqueue(self.con, self.ev, synthetic=True, now_ms=NOW)
        fx = self.con.execute(
            "SELECT attempt_id FROM dispatch_queue WHERE synthetic=1"
        ).fetchall()
        self.con.commit()
        self.con.execute("DELETE FROM dispatch_queue")
        self.con.commit()
        D.enqueue(self.con, self.ev, synthetic=False, now_ms=NOW)
        pr = self.con.execute(
            "SELECT attempt_id FROM dispatch_queue WHERE synthetic=0"
        ).fetchall()
        self.assertEqual(len(fx), 2)
        self.assertEqual(len(pr), 2)
        self.assertFalse({a for (a,) in fx} & {a for (a,) in pr})

    def test_fixture_reservation_does_not_block_production(self):
        last = [0]
        D.enqueue(self.con, self.ev, synthetic=True, now_ms=NOW)
        r1 = D.reserve_pair(self.con, self.ev["event_id"], synthetic=True,
                            now_ms=NOW)
        self.assertEqual(r1["status"], "ready")
        D.enqueue(self.con, self.ev, synthetic=False, now_ms=NOW)
        real_legacy = D._legacy_calls_today
        D._legacy_calls_today = lambda _now: 0
        try:
            r2 = D.reserve_pair(self.con, self.ev["event_id"],
                                synthetic=False, now_ms=NOW)
        finally:
            D._legacy_calls_today = real_legacy
        self.assertEqual(r2["status"], "ready",
                         "a fixture run must never consume the live pair")
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM dispatch_reservations "
                             "WHERE synthetic=1").fetchone()[0], 2)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM dispatch_reservations "
                             "WHERE synthetic=0").fetchone()[0], 2)

    def test_queue_keeps_one_row_per_arm_and_bucket(self):
        D.enqueue(self.con, self.ev, synthetic=True, now_ms=NOW)
        again = D.enqueue(self.con, self.ev, synthetic=False, now_ms=NOW)
        self.assertEqual(again["queued"], 2,
                         "production arms must get their own rows")
        third = D.enqueue(self.con, self.ev, synthetic=False, now_ms=NOW)
        self.assertEqual(third["queued"], 0,
                         "UNIQUE(event_id,arm,synthetic) blocks re-queueing")
        rows = self.con.execute(
            "SELECT synthetic,COUNT(*) FROM dispatch_queue GROUP BY synthetic"
        ).fetchall()
        self.assertEqual(rows, [(0, 2), (1, 2)])


if __name__ == "__main__":
    unittest.main()


class PairCooldownTests(unittest.TestCase):
    """Five plugin contexts = five 15-minute timers. They must not drain cap."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.con = D.open_db(self.db, create=True)

    def tearDown(self):
        self.con.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db + suf)
            except FileNotFoundError:
                pass

    def _ev(self, i):
        return {"event_id": f"{i:064d}", "source": "x",
                "cohort": "context_news", "published_ms": NOW - 60_000,
                "observed_ms": NOW - 50_000, "title": "t", "summary": "s"}

    def test_concurrent_timers_are_held_by_one_lease(self):
        with mock.patch.object(D, "_legacy_calls_today", lambda _n: 0):
            results = []
            for i in range(5):
                ev = self._ev(i + 1)
                D.enqueue(self.con, ev, synthetic=False, now_ms=NOW)
                results.append(D.reserve_pair(self.con, ev["event_id"],
                                              synthetic=False, now_ms=NOW))
            self.assertEqual(results[0]["status"], "ready")
            self.assertEqual([r.get("reason") for r in results[1:]],
                             ["pair_lease_held"] * 4,
                             "one pair at a time; the budget stays available")
            self.assertEqual(self.con.execute(
                "SELECT COUNT(*) FROM dispatch_reservations").fetchone()[0], 2)

    def test_second_pair_allowed_when_lease_expires(self):
        with mock.patch.object(D, "_legacy_calls_today", lambda _n: 0):
            ev1, ev2 = self._ev(1), self._ev(2)
            D.enqueue(self.con, ev1, synthetic=False, now_ms=NOW)
            self.assertEqual(D.reserve_pair(self.con, ev1["event_id"],
                                            synthetic=False,
                                            now_ms=NOW)["status"], "ready")
            later = NOW + D.PAIR_LEASE_MS + 1000
            D.enqueue(self.con, ev2, synthetic=False, now_ms=later)
            second = D.reserve_pair(self.con, ev2["event_id"],
                                    synthetic=False, now_ms=later)
            self.assertEqual(second["status"], "ready")
            self.assertEqual(second["pairs_today"], 2)

    def test_expired_lease_never_wedges_the_lane(self):
        # A crashed dispatch leaves a lease behind; it must expire on its own.
        with mock.patch.object(D, "_legacy_calls_today", lambda _n: 0):
            ev = self._ev(7)
            D.enqueue(self.con, ev, synthetic=False, now_ms=NOW)
            D.reserve_pair(self.con, ev["event_id"], synthetic=False,
                           now_ms=NOW)
            self.con.execute("UPDATE dispatch_lease SET expires_ms=?",
                             (NOW - 1,))
            self.con.commit()
            ev2 = self._ev(8)
            D.enqueue(self.con, ev2, synthetic=False, now_ms=NOW + 10)
            self.assertEqual(D.reserve_pair(self.con, ev2["event_id"],
                                            synthetic=False,
                                            now_ms=NOW + 10)["status"],
                             "ready")

    def test_cooldown_does_not_leak_into_synthetic_bucket(self):
        D.enqueue(self.con, self._ev(1), synthetic=False, now_ms=NOW)
        D.reserve_pair(self.con, self._ev(1)["event_id"], synthetic=False,
                       now_ms=NOW)
        fake = self._ev(9)
        D.enqueue(self.con, fake, synthetic=True, now_ms=NOW)
        res = D.reserve_pair(self.con, fake["event_id"], synthetic=True,
                             now_ms=NOW)
        self.assertEqual(res["status"], "ready")

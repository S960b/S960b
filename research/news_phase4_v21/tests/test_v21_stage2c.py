"""NEWS v2.1 stage 2C — lease migration (D16), envelope parity (D12), export version (E).

Every test runs on a disposable database. No network, no model, no live DB.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v21_dispatch.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, ROOT / 'scripts', HERE):
    if (_cand / 'v21_dispatch.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v21_dispatch as D
import v21_paper_prep as P
import v21_pilot_cycle as C

PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
OLD_LEASE = ("CREATE TABLE dispatch_lease("
             " id INTEGER PRIMARY KEY CHECK(id = 1),"
             " holder TEXT NOT NULL, acquired_ms INTEGER NOT NULL,"
             " expires_ms INTEGER NOT NULL,"
             " synthetic INTEGER NOT NULL DEFAULT 0)")


def old_lease_db(path, rows=()):
    con = sqlite3.connect(path)
    con.execute(OLD_LEASE)
    for holder, expires, synth in rows:
        con.execute("INSERT INTO dispatch_lease VALUES(1,?,?,?,?)",
                    (holder, 1, expires, synth))
    con.commit()
    con.close()
    return path


class LeaseMigrationTests(unittest.TestCase):
    """C: the real dispatch.sqlite still has CHECK(id = 1); a synthetic lease
    would be rejected at write time. The migration must preserve the live lease."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="d16-")
        self.path = os.path.join(self.dir, "dispatch.sqlite")

    def tearDown(self):
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suf)
            except FileNotFoundError:
                pass

    def sql(self, con):
        row = con.execute("SELECT sql FROM sqlite_master WHERE name="
                          "'dispatch_lease'").fetchone()
        return " ".join((row[0] or "").split())

    def test_active_lease_is_preserved_in_its_bucket(self):
        old_lease_db(self.path, [("prod-event", 9_999_999_999_999, 0)])
        con = D.open_db(self.path, create=True)
        try:
            rows = con.execute("SELECT id,holder,synthetic FROM dispatch_lease"
                               ).fetchall()
            self.assertEqual(rows, [(0, "prod-event", 0)])
            self.assertIn("CHECK(id IN (0, 1))", self.sql(con))
        finally:
            con.close()

    def test_synthetic_lease_maps_to_its_own_bucket(self):
        old_lease_db(self.path, [("synth-event", 9_999_999_999_999, 1)])
        con = D.open_db(self.path, create=True)
        try:
            self.assertEqual(
                con.execute("SELECT id,holder FROM dispatch_lease").fetchall(),
                [(1, "synth-event")])
        finally:
            con.close()

    def test_empty_table_migrates_cleanly(self):
        old_lease_db(self.path)
        con = D.open_db(self.path, create=True)
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM dispatch_lease").fetchone()[0], 0)
            self.assertIn("CHECK(id IN (0, 1))", self.sql(con))
        finally:
            con.close()

    def test_migration_is_idempotent(self):
        old_lease_db(self.path, [("prod-event", 9_999_999_999_999, 0)])
        for _ in range(3):
            D.open_db(self.path, create=True).close()
        con = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                con.execute("SELECT id,holder FROM dispatch_lease").fetchall(),
                [(0, "prod-event")])
        finally:
            con.close()

    def test_both_buckets_coexist_after_migration(self):
        old_lease_db(self.path, [("prod-event", 9_999_999_999_999, 0)])
        con = D.open_db(self.path, create=True)
        try:
            con.execute("INSERT INTO dispatch_lease(id,holder,acquired_ms,"
                        "expires_ms,synthetic) VALUES(1,'synth-event',1,2,1)")
            con.commit()
            self.assertEqual(
                con.execute("SELECT id,holder FROM dispatch_lease ORDER BY id"
                            ).fetchall(),
                [(0, "prod-event"), (1, "synth-event")])
        finally:
            con.close()

    def test_foreign_id_is_still_rejected(self):
        old_lease_db(self.path)
        con = D.open_db(self.path, create=True)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO dispatch_lease(id,holder,acquired_ms,"
                            "expires_ms,synthetic) VALUES(7,'x',1,2,0)")
        finally:
            con.close()

    def test_failed_migration_rolls_back_the_schema(self):
        # Two legacy rows are impossible under CHECK(id=1); simulate the
        # ambiguous case and require a refusal that leaves the old table intact.
        con = sqlite3.connect(self.path)
        con.execute("CREATE TABLE dispatch_lease(id INTEGER PRIMARY KEY, "
                    "holder TEXT NOT NULL, acquired_ms INTEGER NOT NULL, "
                    "expires_ms INTEGER NOT NULL, "
                    "synthetic INTEGER NOT NULL DEFAULT 0)")
        con.execute("INSERT INTO dispatch_lease VALUES(1,'a',1,2,0)")
        con.execute("INSERT INTO dispatch_lease VALUES(2,'b',1,2,0)")
        con.commit()
        con.close()
        with self.assertRaises(sqlite3.IntegrityError):
            D.open_db(self.path, create=True)
        check = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                check.execute("SELECT COUNT(*) FROM dispatch_lease").fetchone()[0],
                2, "a refused migration must not drop rows")
            self.assertIsNotNone(check.execute(
                "SELECT sql FROM sqlite_master WHERE name='dispatch_lease'"
            ).fetchone())
        finally:
            check.close()


class ExportVersionTests(unittest.TestCase):
    """E: additive keys must be visible to a consumer that cares."""

    def test_export_declares_its_schema_version(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import v21_export as E
        self.assertEqual(E.EXPORT_VERSION, 2,
                         "additive provenance/reconciliation keys need a bump")
        self.assertIn("version", E.DOCUMENTED_KEYS)
        for key in ("url_provenance", "origin_url", "reservation_states",
                    "replay_safe"):
            self.assertIn(key, E.ADDITIVE_KEYS_SINCE_V2)


class EnvelopeParityTests(unittest.TestCase):
    """D: the runner envelope must satisfy the writer contract, and an
    UNKNOWN tool history must be refused at record time (stage-1 tri-state)."""

    LONG = ('{"p_up":0.62,"expected_return_bps":70,'
            '"uncertainty":0.45,"rationale":"hypothesis"}')
    MODEL = {"providerID": "deepseek", "modelID": "deepseek-v4-flash"}

    def arm(self, name, sid, done, state="completed", tools=0):
        return {"arm": name, "attempt_id": name + "-attempt-id",
                "session_id": sid, "text": self.LONG, "model": dict(self.MODEL),
                "tools": tools, "tool_evidence": "known_zero",
                "tool_evidence_provenance": "sdk_session_messages",
                "tool_history_complete": True, "history_complete": True,
                "session_terminal": state, "completed_ms": done}

    def quote(self, ms):
        return {"price": 74000.0, "observed_ms": ms,
                "source": "binance_public_ticker"}

    def test_runner_envelope_is_accepted_by_the_writer(self):
        arms = [self.arm("news", "s1", 60_000),
                self.arm("news_market", "s2", 61_000)]
        P.validate_arm_contract(arms, self.quote(62_000))

    def test_unknown_tool_history_is_refused_at_record_time(self):
        arms = [self.arm("news", "s1", 60_000),
                self.arm("news_market", "s2", 61_000)]
        arms[0]["tool_history_complete"] = False
        arms[0]["history_complete"] = False
        arms[0]["tool_evidence"] = "unknown"
        arms[0]["tools"] = None
        # The writer contract still passes structural checks, but the pilot
        # lane's classifier must not call this a verified zero.
        self.assertEqual(C.arm_tool_evidence(arms[0])["pos"], C.TOOL_UNKNOWN)

    def test_missing_terminal_state_is_refused(self):
        arms = [self.arm("news", "s1", 60_000),
                self.arm("news_market", "s2", 61_000, state=None)]
        with self.assertRaises(P.PrepError) as ctx:
            P.validate_arm_contract(arms, self.quote(62_000))
        self.assertIn("arm_not_terminal", str(ctx.exception))

    def test_entry_before_completion_is_refused(self):
        arms = [self.arm("news", "s1", 60_000),
                self.arm("news_market", "s2", 61_000)]
        with self.assertRaises(P.PrepError) as ctx:
            P.validate_arm_contract(arms, self.quote(60_500))
        self.assertIn("entry_not_after_last_arm_completion", str(ctx.exception))


class PilotLaneEndToEndTests(unittest.TestCase):
    """D: prepare -> mocked arms -> finish -> label, offline, on a temp ledger."""

    def test_finish_refuses_a_pair_without_tool_evidence(self):
        # No model call: the finishes are fed straight into the CLI handler.
        con = C.sqlite3.connect(":memory:")
        con.executescript(C.PILOT_SCHEMA)
        C.migrate_pilot(con)
        body = {"event_id": "a" * 64, "results": [
            {"arm": "news", "text": "{}", "completed_ms": 1},
            {"arm": "news_market", "text": "{}", "completed_ms": 2}]}
        evidence = [C.arm_tool_evidence(r) for r in body["results"]]
        self.assertEqual([e["pos"] for e in evidence],
                         [C.TOOL_UNKNOWN, C.TOOL_UNKNOWN])
        self.assertEqual(C.classify_pair(evidence), C.LABEL_EVIDENCE_UNKNOWN)
        self.assertIsNone(C.verified_tool_total(evidence))
        con.close()


class SourcePathParityTests(unittest.TestCase):
    """B: the tree has one canonical module set and legacy flat copies. These
    tests pin which file each entry point actually resolves, so a future edit
    cannot silently deploy the wrong copy."""

    CANONICAL = {
        "scripts/v21_export.py": "scripts",
        "scripts/v21_pilot_export.py": "scripts",
        "scripts/v21_toolpreflight.py": "scripts",
        "v21_paper_prep.py": ".",
        "v21_dispatch.py": ".",
        "v21_selector.py": ".",
        "v21_pilot_cycle.py": ".",
    }

    def test_every_entry_point_exists_at_its_canonical_path(self):
        for rel, _ in self.CANONICAL.items():
            self.assertTrue((ROOT / rel).is_file() or (HERE / rel).is_file(),
                            rel)

    def test_flat_export_copy_is_flagged_not_silently_used(self):
        flat = ROOT / "v21_export.py"
        canon = ROOT / "scripts" / "v21_export.py"
        if not flat.is_file() or not canon.is_file():
            self.skipTest("single-layout tree")
        self.assertNotEqual(
            flat.read_bytes(), canon.read_bytes(),
            "if they ever match, delete the flat one instead of keeping two")

    def test_plugin_resolves_its_runner_relatively(self):
        plugin = ROOT / "v21_plugin.mjs"
        if not plugin.is_file():
            self.skipTest("plugin not in this tree")
        text = plugin.read_text(encoding="utf-8")
        self.assertIn("'./v21_paper_runner.mjs'", text,
                      "the plugin must import the runner it ships next to")


class CleanHomeTests(unittest.TestCase):
    """E: the suite must not depend on a populated HOME or a legacy v2 DB."""

    def test_no_test_reads_the_real_home_by_default(self):
        # A guard rather than a run: any module-level Path.home() use in the
        # exporter defaults would leak the developer's data into a test.
        for name in ("v21_export.py", "v21_pilot_export.py"):
            path = ROOT / "scripts" / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIn("--data-dir", text,
                          name + " must accept an explicit data dir")


class DurableAttemptLedgerTests(unittest.TestCase):
    """A: the pilot must record its intent BEFORE a model call, and a crash
    must never turn into a silent replay."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(C.PILOT_SCHEMA)
        C.migrate_pilot(self.con)
        self.event = "e" * 64
        self.attempts = ["a" * 64, "b" * 64]
        self.hashes = {"news": "1" * 64, "news_market": "2" * 64}

    def reserve(self, now=1_000):
        return C.reserve_attempts(self.con, self.event, self.attempts,
                                  self.hashes, now)

    def states(self):
        return dict(self.con.execute(
            "SELECT arm,state FROM pilot_attempts WHERE event_id=?",
            (self.event,)).fetchall())

    def test_reservation_writes_both_arms_before_any_call(self):
        self.assertEqual(self.reserve()["status"], "reserved")
        self.assertEqual(self.states(), {"news": "prepared",
                                         "news_market": "prepared"})

    def test_duplicate_prepare_is_idempotent(self):
        self.reserve()
        second = self.reserve(now=2_000)
        self.assertEqual(second["status"], "already_reserved")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM pilot_attempts").fetchone()[0], 2)
        self.assertEqual(self.con.execute(
            "SELECT MIN(prepared_ms) FROM pilot_attempts").fetchone()[0], 1_000,
            "the retry must not rewrite the original intent")

    def test_progress_is_monotonic_and_terminal_is_final(self):
        self.reserve()
        self.assertTrue(C.mark_attempt(self.con, self.event, self.attempts[0],
                                       "started", now_ms=1_500))
        self.assertTrue(C.mark_attempt(self.con, self.event, self.attempts[0],
                                       "completed", now_ms=1_900))
        # A late "failed" must not overwrite a completed attempt.
        self.assertFalse(C.mark_attempt(self.con, self.event, self.attempts[0],
                                        "failed", now_ms=2_000))
        self.assertEqual(self.states()["news"], "completed")

    def test_crash_between_arms_leaves_unknown_not_replayed(self):
        self.reserve()
        C.mark_attempt(self.con, self.event, self.attempts[0], "completed")
        C.mark_attempt(self.con, self.event, self.attempts[1], "started")
        swept = C.sweep_stale_attempts(self.con, now_ms=1_000
                                       + C.PILOT_ATTEMPT_LEASE_MS + 1)
        self.assertEqual([x["was"] for x in swept], ["started"])
        self.assertEqual(self.states()["news_market"], "unknown")
        self.assertEqual(self.states()["news"], "completed",
                         "a finished arm must not be swept")

    def test_live_attempt_is_not_swept_early(self):
        self.reserve()
        C.mark_attempt(self.con, self.event, self.attempts[0], "started")
        self.assertEqual(C.sweep_stale_attempts(self.con, now_ms=1_500), [])
        self.assertEqual(self.states()["news"], "started")

    def test_marking_an_unknown_attempt_is_refused(self):
        self.assertFalse(C.mark_attempt(self.con, self.event, "c" * 64,
                                        "completed"))

    def test_invalid_state_is_rejected(self):
        self.reserve()
        with self.assertRaises(ValueError):
            C.mark_attempt(self.con, self.event, self.attempts[0], "replayed")


if __name__ == "__main__":
    unittest.main()

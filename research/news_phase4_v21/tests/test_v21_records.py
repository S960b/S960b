"""NEWS v2.1 — end-to-end record tests (offline, temp DBs, no model).

Simulates one full paired decision without any network call: prepare -> two arm
texts -> parse -> gate -> quote -> immutable record -> matured label.
"""
import os
import sqlite3
import tempfile
import unittest

import v21_dispatch as D
import v21_paper_prep as P

NOW = 1_800_000_000_000
HOUR = 3_600_000
CLOSES = [100_000 + i for i in range(26)]
LAST_OPEN = ((NOW - 5 * 60_000) // HOUR) * HOUR - HOUR
CANDLES = [(LAST_OPEN - (25 - i) * HOUR,
            LAST_OPEN - (25 - i) * HOUR + HOUR - 1000, p)
           for i, p in enumerate(CLOSES)]

LONG_TEXT = ('{"p_up":0.62,"expected_return_bps":70,'
             '"uncertainty":0.45,"rationale":"ETF inflows"}')
SHORT_TEXT = ('{"p_up":0.40,"expected_return_bps":-80,'
              '"uncertainty":0.55,"rationale":"outflows"}')
MODEL = {"providerID": "deepseek", "modelID": "deepseek-v4-flash"}


def event(**kw):
    e = {"event_id": "a" * 64, "source": "coindesk", "cohort": "context_news",
         "published_ms": NOW - 600_000, "observed_ms": NOW - 540_000,
         "title": "Bitcoin ETF inflows hit a record", "summary": "Spot demand"}
    e.update(kw)
    return e


def arm_result(prep, index, arm, session, text, model=MODEL, done=20_000):
    """One arm as the runner really hands it over (D12 contract)."""
    return {"arm": arm, "attempt_id": prep["attempts"][index],
            "session_id": session, "text": text, "model": model,
            "session_terminal": "completed", "completed_ms": NOW + done}


class RecordTests(unittest.TestCase):
    def setUp(self):
        fd, self.dispatch_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        fd, self.rec_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.dcon = D.open_db(self.dispatch_db, create=True)
        self.rcon = P.open_records(self.rec_db)

    def tearDown(self):
        for con in (self.dcon, self.rcon):
            con.close()
        for db in (self.dispatch_db, self.rec_db):
            for suf in ("", "-wal", "-shm"):
                try:
                    os.unlink(db + suf)
                except FileNotFoundError:
                    pass

    def _prepare(self, ev=None):
        return P.prepare(None, self.dcon, ev or event(), enabled=True,
                         now_ms=NOW, market=P.market_features(CANDLES, NOW),
                         synthetic=True)

    def test_full_pair_recorded_with_quote(self):
        ev = event()
        prep = self._prepare(ev)
        self.assertEqual(prep["status"], "ready")
        # D12: the writer now refuses arms without a terminal state and a
        # completion time, so the fixture carries what the runner really sends.
        arms = [
            {"arm": "news", "attempt_id": prep["attempts"][0],
             "session_id": "ses_a", "text": LONG_TEXT, "model": MODEL,
             "session_terminal": "completed", "completed_ms": NOW + 20_000},
            {"arm": "news_market", "attempt_id": prep["attempts"][1],
             "session_id": "ses_b", "text": SHORT_TEXT, "model": MODEL,
             "session_terminal": "completed", "completed_ms": NOW + 21_000},
        ]
        quote = {"price": P.parse_quote({"symbol": "BTCUSDT",
                                         "price": "74000.00"}),
                 "observed_ms": NOW + 30_000}
        out = P.record_pair(self.rcon, event=ev, prepared=prep,
                            arm_results=arms, quote=quote,
                            now_ms=NOW + 31_000, synthetic=True)
        self.assertEqual(out["status"], "recorded")
        self.assertEqual(len(out["seal"]), 64)
        rows = self.rcon.execute(
            "SELECT arm,action,p_up FROM paper_arms WHERE event_id=? "
            "ORDER BY arm", (ev["event_id"],)).fetchall()
        self.assertEqual(rows, [("news", "LONG_PAPER", 0.62),
                                ("news_market", "SHORT_PAPER", 0.40)])

    def test_model_identity_conflict_blocks_record(self):
        ev = event()
        prep = self._prepare(ev)
        arms = [
            arm_result(prep, 0, "news", "ses_a", LONG_TEXT,
                       model={"providerID": "deepseek",
                              "modelID": "deepseek-v4-flash"}),
            arm_result(prep, 1, "news_market", "ses_b", SHORT_TEXT,
                       model={"providerID": "other", "modelID": "other-model"},
                       done=21_000),
        ]
        with self.assertRaises(P.PrepError):
            P.record_pair(self.rcon, event=ev, prepared=prep,
                          arm_results=arms,
                          quote={"price": 74000.0, "observed_ms": NOW},
                          now_ms=NOW, synthetic=True)

    def test_incomplete_pair_is_not_recorded(self):
        ev = event()
        prep = self._prepare(ev)
        with self.assertRaises(P.PrepError):
            P.record_pair(self.rcon, event=ev, prepared=prep,
                          arm_results=[{"arm": "news",
                                        "attempt_id": prep["attempts"][0],
                                        "session_id": "s", "text": LONG_TEXT,
                                        "model": MODEL}],
                          quote={"price": 74000.0, "observed_ms": NOW},
                          now_ms=NOW, synthetic=True)

    def test_outcome_label_persisted(self):
        ev = event()
        prep = self._prepare(ev)
        arms = [
            arm_result(prep, 0, "news", "s1", LONG_TEXT),
            arm_result(prep, 1, "news_market", "s2", SHORT_TEXT, done=21_000),
        ]
        P.record_pair(self.rcon, event=ev, prepared=prep, arm_results=arms,
                      quote={"price": 74000.0, "observed_ms": NOW + 22_000},
                      now_ms=NOW + 22_000, synthetic=True)
        P.record_outcome(self.rcon, ev["event_id"], 4, NOW + 4 * HOUR,
                         74500.0, NOW + 4 * HOUR + 120_000)
        row = self.rcon.execute(
            "SELECT horizon_h,price FROM paper_outcomes").fetchone()
        self.assertEqual(row, (4, 74500.0))

    def test_paper_net_from_stored_action(self):
        # LONG into +67 bps -> 67 - 25 = 42
        ret = 10_000 * (74500.0 / 74000.0 - 1)
        self.assertAlmostEqual(P.paper_net_bps("LONG_PAPER", ret), ret - 25)

    def test_synthetic_flag_preserved(self):
        ev = event()
        prep = self._prepare(ev)
        arms = [
            arm_result(prep, 0, "news", "s1", LONG_TEXT),
            arm_result(prep, 1, "news_market", "s2", SHORT_TEXT, done=21_000),
        ]
        P.record_pair(self.rcon, event=ev, prepared=prep, arm_results=arms,
                      quote={"price": 74000.0, "observed_ms": NOW + 22_000},
                      now_ms=NOW + 22_000, synthetic=True)
        flag = self.rcon.execute(
            "SELECT synthetic FROM paper_records").fetchone()[0]
        self.assertEqual(flag, 1, "fixtures must be marked, never look real")


if __name__ == "__main__":
    unittest.main()


class ImmutabilityTests(RecordTests):
    def _pair(self, ev, prep):
        arms = [
            arm_result(prep, 0, "news", "s1", LONG_TEXT),
            arm_result(prep, 1, "news_market", "s2", SHORT_TEXT, done=25_000),
        ]
        return P.record_pair(self.rcon, event=ev, prepared=prep,
                             arm_results=arms,
                             quote={"price": 74000.0,
                                    "observed_ms": NOW + 30_000},
                             now_ms=NOW + 30_000, synthetic=True)

    def test_second_record_is_refused_not_overwritten(self):
        ev = event()
        prep = self._prepare(ev)
        first = self._pair(ev, prep)
        self.assertEqual(first["status"], "recorded")
        second = self._pair(ev, prep)
        self.assertEqual(second["status"], "already_recorded")
        self.assertEqual(second["seal"], first["seal"])
        self.assertEqual(self.rcon.execute(
            "SELECT COUNT(*) FROM paper_records").fetchone()[0], 1)

    def test_outcome_is_not_overwritten(self):
        ev = event()
        prep = self._prepare(ev)
        self._pair(ev, prep)
        P.record_outcome(self.rcon, ev["event_id"], 4, NOW + 4 * HOUR, 74500.0,
                         NOW + 4 * HOUR + 1000)
        again = P.record_outcome(self.rcon, ev["event_id"], 4, NOW + 4 * HOUR,
                                 99000.0, NOW + 9 * HOUR)
        self.assertEqual(again["status"], "already_settled")
        row = self.rcon.execute(
            "SELECT price FROM paper_outcomes").fetchone()
        self.assertEqual(row[0], 74500.0, "first label wins")

    def test_outcome_without_record_refused(self):
        with self.assertRaises(P.PrepError):
            P.record_outcome(self.rcon, "z" * 64, 4, NOW, 1.0, NOW)

    def test_reconciliation_read_is_complete(self):
        ev = event()
        prep = self._prepare(ev)
        self._pair(ev, prep)
        P.record_outcome(self.rcon, ev["event_id"], 4, NOW + 4 * HOUR, 74500.0,
                         NOW + 4 * HOUR + 1000)
        rec = P.read_record(self.rcon, ev["event_id"])
        self.assertEqual(rec["state"], "complete")
        self.assertEqual(rec["model"], "deepseek-v4-flash")
        self.assertEqual(rec["entry"]["price"], 74000.0)
        self.assertEqual(len(rec["arms"]), 2)
        self.assertEqual(len(rec["outcomes"]), 1)
        self.assertTrue(rec["synthetic"])
        for arm in rec["arms"]:
            self.assertEqual(len(arm["response_sha256"]), 64)
            self.assertIsNotNone(arm["completed_ms"])


class TimestampTests(RecordTests):
    def test_completed_ms_recorded_per_arm(self):
        ev = event()
        prep = self._prepare(ev)
        arms = [
            {"arm": "news", "attempt_id": prep["attempts"][0],
             "session_id": "s1", "text": LONG_TEXT, "model": MODEL,
             "completed_ms": NOW + 12_000},
            {"arm": "news_market", "attempt_id": prep["attempts"][1],
             "session_id": "s2", "text": SHORT_TEXT, "model": MODEL,
             "completed_ms": NOW + 47_000},
        ]
        P.record_pair(self.rcon, event=ev, prepared=prep, arm_results=arms,
                      quote={"price": 74000.0, "observed_ms": NOW + 48_000},
                      now_ms=NOW + 49_000, synthetic=True)
        rows = dict(self.rcon.execute(
            "SELECT arm,completed_ms FROM paper_arms").fetchall())
        self.assertEqual(rows["news"], NOW + 12_000)
        self.assertEqual(rows["news_market"], NOW + 47_000)
        self.assertNotEqual(rows["news"], rows["news_market"])

    def test_entry_quote_time_is_the_one_after_both_arms(self):
        ev = event()
        prep = self._prepare(ev)
        arms = [
            {"arm": "news", "attempt_id": prep["attempts"][0],
             "session_id": "s1", "text": LONG_TEXT, "model": MODEL,
             "completed_ms": NOW + 12_000},
            {"arm": "news_market", "attempt_id": prep["attempts"][1],
             "session_id": "s2", "text": SHORT_TEXT, "model": MODEL,
             "completed_ms": NOW + 47_000},
        ]
        P.record_pair(self.rcon, event=ev, prepared=prep, arm_results=arms,
                      quote={"price": 74000.0, "observed_ms": NOW + 48_000},
                      now_ms=NOW + 49_000, synthetic=True)
        entry = self.rcon.execute(
            "SELECT price,observed_ms FROM paper_entries").fetchone()
        self.assertEqual(entry, (74000.0, NOW + 48_000))
        rec = P.read_record(self.rcon, ev["event_id"])
        self.assertEqual(rec["entry"]["observed_ms"], NOW + 48_000)
        last_arm = max(a["completed_ms"] for a in rec["arms"])
        self.assertGreaterEqual(rec["entry"]["observed_ms"], last_arm)


class AmbiguousResponseTests(unittest.TestCase):
    """A model answer must resolve to exactly one decision object, or fail."""

    OK = '{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.6,"rationale":"x"}'

    def test_prose_before_json_is_accepted(self):
        parsed = P.parse_arm_response("Модель рассуждает. " + self.OK)
        self.assertEqual(parsed["p_up"], 0.5)

    def test_two_valid_objects_are_refused(self):
        text = (self.OK + " ещё " +
                '{"p_up":0.9,"expected_return_bps":500,'
                '"uncertainty":0.1,"rationale":"y"}')
        with self.assertRaises(P.PrepError) as ctx:
            P.parse_arm_response(text)
        self.assertEqual(str(ctx.exception),
                         "ambiguous_response_multiple_objects")

    def test_lookalike_junk_plus_one_object_is_accepted(self):
        text = "см. { не json } и вот: " + self.OK
        self.assertEqual(P.parse_arm_response(text)["p_up"], 0.5)

    def test_braces_inside_strings_do_not_confuse_matching(self):
        text = ('{"p_up":0.7,"expected_return_bps":10,"uncertainty":0.2,'
                '"rationale":"brace } and { inside"}')
        self.assertEqual(P.parse_arm_response(text)["p_up"], 0.7)

    def test_no_object_fails(self):
        with self.assertRaises(P.PrepError):
            P.parse_arm_response("никакого объекта тут нет")

    def test_extra_field_fails(self):
        text = ('{"p_up":0.5,"expected_return_bps":0,"uncertainty":0.6,'
                '"rationale":"x","extra":1}')
        with self.assertRaises(P.PrepError):
            P.parse_arm_response(text)

    def test_out_of_range_still_fails(self):
        with self.assertRaises(P.PrepError):
            P.parse_arm_response(
                '{"p_up":1.7,"expected_return_bps":0,"uncertainty":0.6,'
                '"rationale":"x"}')


class OutcomeSchemaMigrationTests(unittest.TestCase):
    """A pre-existing paper_outcomes table must gain the audit columns.

    The label writer crashed at the first due instant because the table had been
    created before due_ms/drift_ms/method existed, and CREATE TABLE IF NOT
    EXISTS leaves an old table untouched. Silence was the real defect.
    """

    def test_old_table_is_migrated_in_place(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        legacy = sqlite3.connect(path)
        legacy.executescript("""
          CREATE TABLE paper_outcomes(
            event_id TEXT NOT NULL, horizon_h INTEGER NOT NULL,
            exit_ms INTEGER NOT NULL, price REAL NOT NULL,
            settled_ms INTEGER NOT NULL, observed_ms INTEGER NOT NULL,
            PRIMARY KEY(event_id, horizon_h));
          INSERT INTO paper_outcomes VALUES('legacy', 4, 1, 2.0, 3, 4);
        """)
        legacy.commit()
        legacy.close()
        con = P.open_records(path)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(paper_outcomes)")}
            for name in ("due_ms", "drift_ms", "method"):
                self.assertIn(name, cols, f"{name} must be added")
            kept = con.execute(
                "SELECT price FROM paper_outcomes WHERE event_id='legacy'"
            ).fetchone()[0]
            self.assertEqual(kept, 2.0, "existing rows must be preserved")
        finally:
            con.close()
            os.unlink(path)

    def test_migration_is_idempotent(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        for _ in range(3):
            con = P.open_records(path)
            con.close()
        con = P.open_records(path)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(paper_outcomes)")]
            self.assertEqual(cols.count("due_ms"), 1)
        finally:
            con.close()
            os.unlink(path)

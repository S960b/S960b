"""NEWS v2.1 — tool evidence is tri-state, and absence is never a zero.

The pilot ledger used to write `sum(int(r.get("tools", 0)))`, so an arm whose
tool history was missing, null, truncated or unreadable produced the same
"0 tools, compliant" row as an arm that was actually observed to call nothing.
These tests pin the three states and the refusal to invent a zero.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import v21_pilot_cycle as C

GOOD = {"tools": 0, "tool_history_complete": True, "session_id": "ses_1",
        "session_terminal": "idle", "tool_evidence_provenance": "sdk"}


def arm(**over):
    ev = dict(GOOD)
    ev.update(over)
    return ev


class EvidenceStateTests(unittest.TestCase):
    def test_missing_field_is_unknown(self):
        ev = C.arm_tool_evidence({"session_id": "s", "session_terminal": "idle"})
        self.assertEqual(ev["pos"], C.TOOL_UNKNOWN)
        self.assertIn("missing_tool_history", ev["provenance"])
        self.assertIsNone(ev["count"])

    def test_null_is_unknown(self):
        ev = C.arm_tool_evidence(arm(tools=None))
        self.assertEqual(ev["pos"], C.TOOL_UNKNOWN)
        self.assertIsNone(ev["count"])

    def test_empty_but_incomplete_history_is_unknown(self):
        # A gateway-reported zero without proof that the history was complete.
        ev = C.arm_tool_evidence(arm(tool_history_complete=False))
        self.assertEqual(ev["pos"], C.TOOL_UNKNOWN)
        self.assertIn("history_not_proven_complete", ev["provenance"])

    def test_sentinel_error_is_unknown_not_negative(self):
        ev = C.arm_tool_evidence(arm(tools=-1))
        self.assertEqual(ev["pos"], C.TOOL_UNKNOWN)
        self.assertIn("sentinel", ev["provenance"])

    def test_bool_and_string_are_unknown(self):
        for raw in (True, "0", 0.0, {}):
            ev = C.arm_tool_evidence(arm(tools=raw))
            self.assertEqual(ev["pos"], C.TOOL_UNKNOWN, raw)

    def test_non_mapping_is_unknown(self):
        self.assertEqual(C.arm_tool_evidence(None)["pos"], C.TOOL_UNKNOWN)
        self.assertEqual(C.arm_tool_evidence("x")["pos"], C.TOOL_UNKNOWN)

    def test_true_zero_is_known_zero(self):
        ev = C.arm_tool_evidence(arm())
        self.assertEqual(ev["pos"], C.TOOL_KNOWN_ZERO)
        self.assertEqual(ev["count"], 0)
        self.assertTrue(ev["session_verified"])

    def test_positive_usage_is_known_positive(self):
        ev = C.arm_tool_evidence(arm(tools=3))
        self.assertEqual(ev["pos"], C.TOOL_KNOWN_POSITIVE)
        self.assertEqual(ev["count"], 3)

    def test_unverified_session_blocks_compliance(self):
        ev = C.arm_tool_evidence(arm(session_terminal=None))
        self.assertEqual(ev["pos"], C.TOOL_KNOWN_ZERO)
        self.assertFalse(ev["session_verified"])


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.zero = C.arm_tool_evidence(arm())
        self.unknown = C.arm_tool_evidence({})
        self.positive = C.arm_tool_evidence(arm(tools=1))

    def test_both_verified_zero_is_compliant(self):
        self.assertEqual(C.classify_pair([self.zero, self.zero]),
                         C.LABEL_COMPLIANT)

    def test_one_unknown_never_compliant(self):
        self.assertEqual(C.classify_pair([self.zero, self.unknown]),
                         C.LABEL_EVIDENCE_UNKNOWN)

    def test_positive_leaks(self):
        self.assertEqual(C.classify_pair([self.zero, self.positive]),
                         C.LABEL_TOOL_LEAK)

    def test_unverified_session_is_its_own_label(self):
        ev = C.arm_tool_evidence(arm(session_terminal=None))
        self.assertEqual(C.classify_pair([self.zero, ev]),
                         C.LABEL_SESSION_UNVERIFIED)

    def test_verified_total_is_none_when_any_unknown(self):
        self.assertIsNone(C.verified_tool_total([self.zero, self.unknown]))
        self.assertEqual(C.verified_tool_total([self.zero, self.zero]), 0)
        self.assertEqual(C.verified_tool_total([self.zero, self.positive]), 1)


class LedgerTests(unittest.TestCase):
    """The schema addition is additive; historical rows stay as they were."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(C.PILOT_SCHEMA)
        C.migrate_pilot(self.con)

    def test_evidence_columns_exist(self):
        have = {r[1] for r in self.con.execute("PRAGMA table_info(pilot_arms)")}
        for name, _t in C.ARM_EVIDENCE_COLUMNS:
            self.assertIn(name, have)

    def test_migration_is_idempotent(self):
        C.migrate_pilot(self.con)
        C.migrate_pilot(self.con)

    def test_annotations_are_append_only_and_deduplicated(self):
        self.con.execute(
            "INSERT INTO pilot_annotations(event_id,version,kind,at_ms,detail) "
            "VALUES('e','tool-evidence-v1','correction',1,'{}')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO pilot_annotations(event_id,version,kind,at_ms,"
                "detail) VALUES('e','tool-evidence-v1','correction',2,'{}')")

    def test_old_row_reads_as_unknown(self):
        # A row written before the columns existed keeps NULL.
        self.con.execute(
            "INSERT INTO pilot_arms(event_id,arm,p_up,expected_return_bps,"
            "uncertainty,action,response_sha256,completed_ms,latency_ms) "
            "VALUES('e','news',0.5,0,0.5,'LONG','sha',1,1)")
        ev = self.con.execute(
            "SELECT tool_evidence FROM pilot_arms WHERE event_id='e'").fetchone()[0]
        self.assertIsNone(ev)
        self.assertNotIn(ev, C.TOOL_EVIDENCE_STATES)


if __name__ == "__main__":
    unittest.main()

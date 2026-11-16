"""NEWS v2.2 stage 3 — three-arm harness, offline only.

No model call, no network, no production DB. Every database here is a temp file
or in-memory. The M arm is checked for leakage mechanically, because a silent
leak would turn the control into a second news arm.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v22_arms.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, HERE):
    if (_cand / 'v22_arms.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v21_paper_prep as P
import v22_analyzer as AN
import v22_arms as A
import v22_ledger as L

H = 3_600_000
CANDLES = [(i * H, i * H + H - 1, 60000.0 + i * 10) for i in range(30)]
MARKET = P.market_features(CANDLES, 26 * H)


def event(**over):
    e = {"event_id": "a" * 64, "source": "coindesk",
         "cohort": "context_news", "source_kind": "media",
         "published_ms": 900_000, "observed_ms": 950_000,
         "title": "Bitcoin ETF approval sends BTC higher",
         "summary": "Institutional inflows accelerate"}
    e.update(over)
    return e


ACTIONS = {"news": (0.62, 70, 0.45),
           "news_market": (0.58, 50, 0.40),
           "market": (0.51, 10, 0.55)}


class MarketArmLeakTests(unittest.TestCase):
    """The M arm must carry no news surface at all."""

    def test_three_distinct_prompts(self):
        prompts = A.build_prompts_3(event(), MARKET)
        self.assertEqual(len(prompts), 3)
        self.assertEqual(len(set(prompts.values())), 3)

    def test_market_prompt_has_no_news_fields(self):
        prompts = A.build_prompts_3(event(), MARKET)
        body = json.loads(prompts["market"].split("FROZEN_INPUT_JSON:\n", 1)[1])
        self.assertEqual(sorted(body),
                         ["arm_label", "horizon_h", "policy",
                          "pre_cutoff_market", "symbol"])

    def test_market_prompt_contains_no_event_strings(self):
        ev = event(title="Bitcoin SEC lawsuit against exchange",
                   summary="Regulator alleges unregistered securities offering")
        prompts = A.build_prompts_3(ev, MARKET)
        text = prompts["market"].lower()
        for needle in ("bitcoin", "sec lawsuit", "coindesk", "a" * 64,
                       "context_news", "media"):
            self.assertNotIn(needle, text)

    def test_injected_news_field_is_refused(self):
        # A future edit that adds a news field to the M payload must fail loudly.
        prompts = A.build_prompts_3(event(), MARKET)
        poisoned = prompts["market"].replace(
            '"arm_label"', '"news":"BTC pumps","arm_label"')
        with self.assertRaises(P.PrepError) as ctx:
            A.verify_market_prompt(poisoned, event())
        self.assertIn("market_prompt", str(ctx.exception))

    def test_market_and_news_market_share_the_same_vector(self):
        prompts = A.build_prompts_3(event(), MARKET)
        nm = json.loads(prompts["news_market"].split("FROZEN_INPUT_JSON:\n", 1)[1])
        m = json.loads(prompts["market"].split("FROZEN_INPUT_JSON:\n", 1)[1])
        self.assertEqual(nm["pre_cutoff_market"], m["pre_cutoff_market"])

    def test_degenerate_market_refused(self):
        # A stub with the right count but none of the features is not a market
        # vector; the arm plan must refuse it rather than prompt on nothing.
        with self.assertRaises(P.PrepError):
            A.build_prompts_3(event(), {"candle_count": 26})
        with self.assertRaises(P.PrepError):
            A.build_prompts_3(event(), {"candle_count": 5})


class TripleLedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v22-")
        self.path = os.path.join(self.dir, "v22.sqlite")
        self.con = L.open_v22(self.path)

    def tearDown(self):
        self.con.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suf)
            except FileNotFoundError:
                pass

    def seal(self, results, quote=None):
        prompts = A.build_prompts_3(event(), MARKET)
        prepared = {"prompts_sha256": A.prompts_sha256(prompts),
                    "cutoff_ms": 999_999}
        return L.seal_triple(self.con, event=event(), prepared=prepared,
                             arm_results=results,
                             quote=quote or L.mock_quote(), now_ms=2_000_000)

    def test_complete_triple_is_sealed(self):
        out = self.seal(L.mock_arm_results("a" * 64, ACTIONS))
        self.assertEqual(out["status"], "sealed")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v22_arms").fetchone()[0], 3)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v22_outcomes").fetchone()[0], 1)

    def test_missing_arm_is_refused(self):
        results = L.mock_arm_results("a" * 64, ACTIONS)[:2]
        with self.assertRaises(P.PrepError) as ctx:
            self.seal(results)
        self.assertIn("incomplete_triple", str(ctx.exception))

    def test_duplicate_arm_is_refused(self):
        results = L.mock_arm_results("a" * 64, ACTIONS)
        results[2] = dict(results[1])
        with self.assertRaises(P.PrepError):
            self.seal(results)

    def test_unknown_tool_evidence_blocks_the_seal(self):
        results = L.mock_arm_results("a" * 64, ACTIONS)
        results[0]["tool_evidence"] = "unknown"
        with self.assertRaises(P.PrepError) as ctx:
            self.seal(results)
        self.assertIn("tool_evidence", str(ctx.exception))

    def test_positive_tool_usage_blocks_the_seal(self):
        results = L.mock_arm_results("a" * 64, ACTIONS)
        results[2]["tool_evidence"] = "known_positive"
        with self.assertRaises(P.PrepError):
            self.seal(results)

    def test_late_entry_is_refused(self):
        results = L.mock_arm_results("a" * 64, ACTIONS)
        last = max(r["completed_ms"] for r in results)
        with self.assertRaises(P.PrepError) as ctx:
            self.seal(results, quote=L.mock_quote(observed_ms=last))
        self.assertIn("entry_not_after_last_arm", str(ctx.exception))

    def test_second_seal_is_idempotent(self):
        first = self.seal(L.mock_arm_results("a" * 64, ACTIONS))
        second = self.seal(L.mock_arm_results("a" * 64, {
            "news": (0.9, 100, 0.1), "news_market": (0.9, 100, 0.1),
            "market": (0.1, -100, 0.1)}))
        self.assertEqual(first["status"], "sealed")
        self.assertEqual(second["status"], "already_sealed")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v22_triples").fetchone()[0], 1)

    def test_v21_two_arm_path_is_untouched(self):
        # The v2.1 builder still returns exactly two arms; no accidental third.
        legacy = P.build_prompts(event(), MARKET)
        self.assertEqual(sorted(legacy), ["news", "news_market"])

    def test_v21_schema_absent_from_v22_ledger(self):
        tables = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("paper_records", tables)
        self.assertNotIn("pilot_pairs", tables)


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.con = L.open_v22(":memory:")

    def tearDown(self):
        self.con.close()

    def _row(self, event_id, arms, outcome_up, day=0):
        self.con.execute("INSERT INTO v22_arms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (event_id, "news", "x", "s", "p", "r",
                          arms[0], 0.0, 0.5, "FLAT", "known_zero", "t", 1))
        return event_id

    def test_proper_scores_punish_confident_errors(self):
        good = AN.brier(0.9, True)
        bad = AN.brier(0.1, True)
        self.assertLess(good, bad)
        self.assertGreater(AN.log_loss(0.99, False), AN.log_loss(0.6, False))

    def test_empty_ledger_is_reported_not_crashed(self):
        out = AN.analyze(self.con)
        self.assertEqual(out["status"], "no_data")

    def test_mde_is_finite_and_shrinks_with_n(self):
        small = AN.minimum_detectable_effect(100)
        large = AN.minimum_detectable_effect(10_000)
        self.assertIsNotNone(small)
        self.assertLess(large, small)
        self.assertAlmostEqual(small, 1.4 / 10.0, places=1)

    def test_pnl_charges_costs_only_when_trading(self):
        self.assertEqual(AN.pnl_bps("FLAT", 64000, 70000), 0.0)
        long_pnl = AN.pnl_bps("LONG_PAPER", 64000, 64640)
        self.assertAlmostEqual(long_pnl, 100 - 6, places=6)
        short_pnl = AN.pnl_bps("SHORT_PAPER", 64000, 64640)
        self.assertAlmostEqual(short_pnl, -100 - 6, places=6)


class BackwardCompatibilityTests(unittest.TestCase):
    def test_v21_prompt_hashes_are_stable(self):
        # The v2.1 header must not drift just because v2.2 was added.
        legacy = P.build_prompts(event(), MARKET)
        self.assertTrue(all(v.startswith("You are researching BTCUSDT")
                            for v in legacy.values()))
        self.assertNotIn("ARM:", list(legacy.values())[0].split("\n")[0])

    def test_v22_uses_its_own_version_string(self):
        self.assertNotEqual(A.PROMPT_VERSION, P.PROMPT_SYSTEM_VERSION)
        self.assertNotEqual(A.PROMPT_VERSION, P.PROMPT_ONLY_VERSION)


if __name__ == "__main__":
    unittest.main()

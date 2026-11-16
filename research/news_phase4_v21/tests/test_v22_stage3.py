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
        hashes = A.prompts_sha256(prompts)
        prepared = {"prompts_sha256": hashes, "cutoff_ms": 999_999}
        # A real writer only seals after the three durable attempts are
        # terminal, so the fixture creates them the same way the runner does.
        L.reserve_triple(self.con, event_id=event()["event_id"],
                         prompts_sha=hashes, now_ms=1_000_000)
        for arm in A.ARM_NAMES:
            L.mark_arm(self.con, event_id=event()["event_id"], arm=arm,
                       state="completed", now_ms=1_000_100)
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
        prompts = A.build_prompts_3(event(), MARKET)
        prepared = {"prompts_sha256": A.prompts_sha256(prompts),
                    "cutoff_ms": 999_999}
        second = L.seal_triple(self.con, event=event(), prepared=prepared,
                               arm_results=L.mock_arm_results("a" * 64, {
                                   "news": (0.9, 100, 0.1),
                                   "news_market": (0.9, 100, 0.1),
                                   "market": (0.1, -100, 0.1)}),
                               quote=L.mock_quote(), now_ms=2_000_000)
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


class DurableTripleAttemptTests(unittest.TestCase):
    """Stage 4: reservation before invocation, monotone states, no replay."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v22a-")
        self.path = os.path.join(self.dir, "v22.sqlite")
        self.con = L.open_v22(self.path)
        self.hashes = A.prompts_sha256(A.build_prompts_3(event(), MARKET))
        self.eid = event()["event_id"]

    def tearDown(self):
        self.con.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suf)
            except FileNotFoundError:
                pass

    def test_reservation_writes_all_three_before_any_call(self):
        out = L.reserve_triple(self.con, event_id=self.eid,
                               prompts_sha=self.hashes, now_ms=1_000)
        self.assertEqual(out["status"], "reserved")
        states = dict(self.con.execute(
            "SELECT arm,state FROM v22_attempts WHERE event_id=?",
            (self.eid,)).fetchall())
        self.assertEqual(states, {"news": "prepared",
                                  "news_market": "prepared",
                                  "market": "prepared"})

    def test_duplicate_event_reservation_is_idempotent(self):
        L.reserve_triple(self.con, event_id=self.eid, prompts_sha=self.hashes,
                         now_ms=1_000)
        again = L.reserve_triple(self.con, event_id=self.eid,
                                 prompts_sha=self.hashes, now_ms=9_999)
        self.assertEqual(again["status"], "already_reserved")
        self.assertEqual(self.con.execute(
            "SELECT MIN(prepared_ms) FROM v22_attempts").fetchone()[0], 1_000,
            "a retry must not rewrite the original intent")

    def test_incomplete_prompt_set_is_refused(self):
        with self.assertRaises(P.PrepError):
            L.reserve_triple(self.con, event_id=self.eid,
                             prompts_sha={"news": "x"}, now_ms=1_000)

    def test_state_machine_is_monotone_and_terminal(self):
        L.reserve_triple(self.con, event_id=self.eid, prompts_sha=self.hashes,
                         now_ms=1_000)
        self.assertTrue(L.mark_arm(self.con, event_id=self.eid, arm="news",
                                   state="started", now_ms=1_100))
        self.assertTrue(L.mark_arm(self.con, event_id=self.eid, arm="news",
                                   state="completed", now_ms=1_200))
        self.assertFalse(L.mark_arm(self.con, event_id=self.eid, arm="news",
                                    state="failed", now_ms=1_300))
        state = self.con.execute("SELECT state FROM v22_attempts WHERE event_id=? "
                                 "AND arm='news'", (self.eid,)).fetchone()[0]
        self.assertEqual(state, "completed")

    def test_fault_injection_between_each_arm(self):
        # A crash at any of the three positions must leave no seal behind.
        for fail_at in ("news", "news_market", "market"):
            with self.subTest(fail_at=fail_at):
                path = os.path.join(self.dir, f"fault-{fail_at}.sqlite")
                out, _ = L.run_offline_triple(path, event=event(),
                                              market=MARKET, actions=ACTIONS,
                                              fail_arm=fail_at)
                self.assertEqual(out["status"], "refused")
                con = L.open_v22(path)
                try:
                    self.assertEqual(con.execute(
                        "SELECT COUNT(*) FROM v22_triples").fetchone()[0], 0)
                finally:
                    con.close()
                    for suf in ("", "-wal", "-shm"):
                        try:
                            os.unlink(path + suf)
                        except FileNotFoundError:
                            pass

    def test_crash_between_arms_leaves_no_seal(self):
        out, _ = L.run_offline_triple(self.path, event=event(), market=MARKET,
                                      actions=ACTIONS, fail_arm="news_market")
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["reason"], "arm_failed:news_market")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v22_triples").fetchone()[0], 0)
        self.assertEqual(self.con.execute(
            "SELECT state FROM v22_attempts WHERE arm='news_market'"
        ).fetchone()[0], "failed")

    def test_resume_after_crash_never_replays(self):
        L.reserve_triple(self.con, event_id=self.eid, prompts_sha=self.hashes,
                         now_ms=1_000)
        L.mark_arm(self.con, event_id=self.eid, arm="news", state="started",
                   now_ms=1_100)
        swept = L.sweep_stale(self.con, now_ms=1_000 + L.ATTEMPT_LEASE_MS + 1)
        # All three arms were prepared at the same instant, so every unfinished
        # one is stale: the started arm and the two never-started ones. None of
        # them is replayed; each becomes unknown with its own reason.
        self.assertEqual(sorted(x["was"] for x in swept),
                         ["prepared", "prepared", "started"])
        self.assertTrue(all(x["event_id"] == self.eid for x in swept))
        state = self.con.execute("SELECT state FROM v22_attempts WHERE event_id=? "
                                 "AND arm='news'", (self.eid,)).fetchone()[0]
        self.assertEqual(state, "unknown")
        # A resume cannot move an unknown arm forward: that would be a replay.
        self.assertFalse(L.mark_arm(self.con, event_id=self.eid, arm="news",
                                    state="completed", now_ms=2_000_000))

    def test_live_attempt_is_not_swept_early(self):
        L.reserve_triple(self.con, event_id=self.eid, prompts_sha=self.hashes,
                         now_ms=1_000)
        self.assertEqual(L.sweep_stale(self.con, now_ms=1_500), [])

    def test_seal_refuses_when_attempts_are_not_terminal(self):
        L.reserve_triple(self.con, event_id=self.eid, prompts_sha=self.hashes,
                         now_ms=1_000)
        prepared = {"prompts_sha256": self.hashes, "cutoff_ms": 999_999}
        with self.assertRaises(P.PrepError) as ctx:
            L.seal_triple(self.con, event=event(), prepared=prepared,
                          arm_results=L.mock_arm_results(self.eid, ACTIONS),
                          quote=L.mock_quote(), now_ms=2_000_000)
        self.assertIn("attempts_not_terminal", str(ctx.exception))

    def test_seal_refuses_without_any_attempt_rows(self):
        prepared = {"prompts_sha256": self.hashes, "cutoff_ms": 999_999}
        with self.assertRaises(P.PrepError) as ctx:
            L.seal_triple(self.con, event=event(), prepared=prepared,
                          arm_results=L.mock_arm_results(self.eid, ACTIONS),
                          quote=L.mock_quote(), now_ms=2_000_000)
        self.assertIn("attempts_incomplete", str(ctx.exception))

    def test_all_terminal_arms_allow_the_seal(self):
        L.reserve_triple(self.con, event_id=self.eid, prompts_sha=self.hashes,
                         now_ms=1_000)
        for arm in A.ARM_NAMES:
            L.mark_arm(self.con, event_id=self.eid, arm=arm, state="completed",
                       now_ms=1_100)
        prepared = {"prompts_sha256": self.hashes, "cutoff_ms": 999_999}
        out = L.seal_triple(self.con, event=event(), prepared=prepared,
                            arm_results=L.mock_arm_results(self.eid, ACTIONS),
                            quote=L.mock_quote(), now_ms=2_000_000)
        self.assertEqual(out["status"], "sealed")


class MethodologyAuditTests(unittest.TestCase):
    """Stage-4 corrections, pinned so the conflation cannot come back."""

    def test_prediction_and_action_baselines_are_separate(self):
        con = L.open_v22(":memory:")
        try:
            scores = AN.score_rows([])
            self.assertEqual(scores, {"n": 0})
            # The climatology key must exist on a populated score set, and the
            # old conflated key must be gone.
            rows = [{"event_id": "e1", "entry_ms": 1, "day": 0, "entry": 100.0,
                     "exit": 101.0, "exit_ms": 2, "drift_ms": 0,
                     "outcome_up": True,
                     "arms": {"news": 0.6, "news_market": 0.55, "market": 0.5}}]
            out = AN.score_rows(rows)
            self.assertIn("climatology_baseline", out)
            self.assertNotIn("zero_trade_baseline", out)
            self.assertIn("base_rate_up_full_sample", out)
        finally:
            con.close()

    def test_climatology_never_uses_the_row_it_predicts(self):
        rows = [{"event_id": f"e{i}", "entry_ms": i, "outcome_up": i % 2 == 0}
                for i in range(4)]
        pairs = AN.walk_forward_climatology(rows)
        self.assertIsNone(pairs[0][1])
        self.assertIsNone(pairs[1][1])
        self.assertIsNotNone(pairs[2][1])
        # The third row's rate may only contain the first two outcomes.
        self.assertEqual(pairs[2][1], 0.5)

    def test_overlap_flag_is_computable(self):
        rows = [{"event_id": "a", "entry_ms": 0, "outcome_up": True},
                {"event_id": "b", "entry_ms": 3_600_000, "outcome_up": False},
                {"event_id": "c", "entry_ms": 8 * 3_600_000, "outcome_up": True}]
        self.assertEqual(AN.overlapping_windows(rows), 1,
                         "b starts inside a's 4h window; c does not")

    def test_flat_never_enters_a_brier_comparison(self):
        # FLAT has no probability, so it cannot be scored as a forecast.
        con = L.open_v22(":memory:")
        try:
            scores = AN.score_rows([{"event_id": "e", "entry_ms": 1, "day": 0,
                                     "entry": 100.0, "exit": 99.0,
                                     "exit_ms": 2, "drift_ms": 0,
                                     "outcome_up": False,
                                     "arms": {"news": 0.4, "news_market": 0.42,
                                              "market": 0.45}}])
            for arm in ("news", "news_market", "market"):
                self.assertIn("brier", scores[arm])
            self.assertNotIn("FLAT", scores)
        finally:
            con.close()

    def test_nomenclature_is_single_sourced(self):
        self.assertEqual(A.PROTOCOL_VERSION, "prospective-v2.2-arms3")
        doc = (ROOT / "docs" / "v22" / "NEWS_V22_PREREGISTRATION.md")
        if not doc.is_file():
            doc = (HERE / "docs_v22" / "NEWS_V22_PREREGISTRATION.md")
        if doc.is_file():
            text = doc.read_text(encoding="utf-8")
            self.assertIn(A.PROTOCOL_VERSION, text)
            self.assertNotIn("prospective-v2.3-arms3", text.replace(
                "`prospective-v2.3-arms3` in one place", ""))

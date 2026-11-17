"""Offline tests for ChatGPT's bounded, disabled-by-default Tier-2 watcher."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "source", ROOT / "source" / "v22_live"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import tier2_bounded_watch as W


class WatchTests(unittest.TestCase):
    def test_single_dry_only_reads(self):
        calls = []
        with patch.object(W.D, "execute_once", side_effect=lambda **kw:
                          calls.append(kw) or {"status": "no_eligible", "model_calls": 0}):
            res = W.watch(feed_db="disposable", single=True, emit=lambda _: None)
        self.assertEqual(res["status"], "no_eligible")
        self.assertEqual(res["ticks"], 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["dry"])

    def test_live_refuses_without_flag_before_poll(self):
        with patch.object(W.TR, "require_enabled",
                          side_effect=W.TR.NotEnabled("tier2_trigger_disabled")), \
             patch.object(W.D, "execute_once",
                          side_effect=AssertionError("must_not_execute")):
            with self.assertRaises(W.TR.NotEnabled):
                W.watch(execute=True, single=True, emit=lambda _: None)

    def test_live_stops_after_exactly_one_seal(self):
        outcomes = iter(({"status": "no_eligible"}, {"status": "sealed", "due_ms": 123}))
        calls = []
        t = [0]
        def run(**kw):
            calls.append(kw)
            return next(outcomes)
        with patch.object(W.TR, "require_enabled", return_value={
                "trial_id": "trial1", "max_triples": 1, "max_model_invocations": 3}), \
             patch.object(W.D, "execute_once", side_effect=run):
            res = W.watch(execute=True, duration_s=200, interval_s=20,
                          clock=lambda: t[0],
                          sleeper=lambda d: t.__setitem__(0, t[0] + d),
                          emit=lambda _: None)
        self.assertEqual(res["status"], "sealed")
        self.assertEqual(res["ticks"], 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c["dry"] is False for c in calls))

    def test_immediate_seal_stops_without_second_poll(self):
        # Models the actual driver return {status: sealed} after on_new_event.
        calls = []
        with patch.object(W.TR, "require_enabled", return_value={
                "trial_id": "trial1", "max_triples": 1, "max_model_invocations": 3}), \
             patch.object(W.D, "execute_once", side_effect=lambda **kw:
                          calls.append(kw) or {"status": "sealed",
                                               "event_id": "evt1",
                                               "due_ms": 1800000}):
            res = W.watch(execute=True, duration_s=120, interval_s=20,
                          emit=lambda _: None)
        self.assertEqual(res["status"], "sealed")
        self.assertEqual(res["ticks"], 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["dry"])

    def test_live_eligible_status_never_triggers_second_poll(self):
        # Defensive guard for any future driver returning this status in live mode.
        calls = []
        with patch.object(W.TR, "require_enabled", return_value={
                "trial_id": "trial1", "max_triples": 1, "max_model_invocations": 3}), \
             patch.object(W.D, "execute_once", side_effect=lambda **kw:
                          calls.append(kw) or {"status": "eligible_observed",
                                               "event_id": "evt1"}):
            res = W.watch(execute=True, duration_s=120, interval_s=20,
                          emit=lambda _: None)
        self.assertEqual(res["status"], "eligible_observed")
        self.assertEqual(res["ticks"], 1)
        self.assertEqual(len(calls), 1)

    def test_failure_is_not_retried(self):
        calls = []
        def broken(**kw):
            calls.append(kw)
            raise OSError("inference_failed")
        with patch.object(W.TR, "require_enabled", return_value={
                "trial_id": "trial1", "max_triples": 1, "max_model_invocations": 3}), \
             patch.object(W.D, "execute_once", side_effect=broken):
            res = W.watch(execute=True, duration_s=100, interval_s=20,
                          emit=lambda _: None)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["ticks"], 1)
        self.assertEqual(len(calls), 1)

    def test_bounded_poll_terminates_at_deadline(self):
        t = [0]
        calls = []
        with patch.object(W.D, "execute_once", side_effect=lambda **kw:
                          calls.append(kw) or {"status": "no_eligible"}):
            res = W.watch(duration_s=45, interval_s=20, clock=lambda: t[0],
                          sleeper=lambda sec: t.__setitem__(0, t[0] + sec),
                          emit=lambda _: None)
        self.assertEqual(res["status"], "deadline")
        self.assertEqual(len(calls), 4)
        self.assertLessEqual(t[0], 45)

    def test_invalid_bounds_rejected(self):
        for duration, interval in ((0, 60), (43201, 60), (60, 0),
                                   (60, 601), (True, 60), (60, True)):
            with self.subTest(duration=duration, interval=interval):
                with self.assertRaises(W.WatchError):
                    W.verify_bounds(duration_s=duration, interval_s=interval,
                                    execute=False)

    def test_wrong_live_budget_refused_before_poll(self):
        with patch.object(W.TR, "require_enabled", return_value={
                "trial_id": "trial1", "max_triples": 10, "max_model_invocations": 3}):
            with self.assertRaises(W.WatchError):
                W.watch(execute=True, single=True, emit=lambda _: None)


if __name__ == "__main__":
    unittest.main()

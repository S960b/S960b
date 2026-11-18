"""Audit R4: F06 no premature label failures, F07 bps, F09 legacy tools.

Entirely disposable SQLite and in-process HTTP fakes. Never calls models.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tier2 as T2
import tier2_label_watch as LW
import v21_paper_prep as P
import v22_toolfree_client as TF
import v22_attestation as AT
import tier2_raw_store as RAW


NOW = 1_800_000_000_000


class LabelBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "tier2.sqlite"
        con = T2.open_tier2(self.db)
        self.addCleanup(con.close)
        event = {"event_id": "f" * 64, "cohort": "geopolitics",
                 "published_ms": NOW - 120_000,
                 "observed_ms": NOW - 60_000}
        self.due = T2.mock_triple(
            con=con, event=event, now_ms=NOW,
            attestation_path=self.root / "attestation.jsonl")["due_ms"]
        self.con = con
        self.target_close = (self.due // 60_000) * 60_000 + 59_999

    def settle(self, at, fetch):
        return LW.settle(db_path=self.db, now_ms=at, candle_fn=fetch,
                         dry=False)["outcomes"][0]

    def attempts(self):
        return LW._attempts_of(self.con, "f" * 64)[0]

    def valid_candle(self, *, due_ms):
        return {
            "symbol": "BTCUSDT", "source": "binance_public_1m_kline",
            "open_ms": self.target_close - 59_999,
            "close_ms": self.target_close,
            "close": 64_000.0, "closed": True,
        }

    def test_F06_wait_before_first_closed_minute_is_zero_attempts(self):
        network_called = []
        def must_not_fetch(*, due_ms):
            network_called.append(due_ms)
            raise AssertionError("before close")
        for step in (0, 1, 5_000):
            when = min(self.target_close, self.due + step)
            got = self.settle(when, must_not_fetch)
            self.assertEqual(got["status"], "pending")
            self.assertEqual(got["reason"], "await_first_closed_candle")
            self.assertEqual(self.attempts(), 0)
        self.assertEqual(network_called, [])
        got = self.settle(self.target_close + 1, self.valid_candle)
        self.assertEqual(got["status"], "labelled")
        self.assertEqual(self.attempts(), 0)

    def test_F06_exchange_pending_open_does_not_spend_budget(self):
        def not_yet(*, due_ms):
            raise P.PrepError("label_pending_candle_open")
        for i in range(5):
            got = self.settle(self.target_close + 1 + i * 1000, not_yet)
            self.assertEqual(got["status"], "pending")
            self.assertEqual(got["reason"], "await_exchange_candle_close")
            self.assertEqual(self.attempts(), 0)
        self.assertEqual(self.settle(self.target_close + 6000,
                                     self.valid_candle)["status"], "labelled")

    def test_F06_real_transport_errors_still_spend_budget_after_close(self):
        def down(*, due_ms):
            raise ConnectionError("offline")
        for i in range(LW.MAX_FAILURES):
            got = self.settle(self.target_close + 1 + i * 1000, down)
        self.assertEqual(got["status"], "censored")
        self.assertEqual(self.attempts(), LW.MAX_FAILURES)


class BpsRangeTests(unittest.TestCase):
    def text(self, bps):
        return json.dumps({
            "p_up": 0.56, "expected_return_bps": bps,
            "uncertainty": 0.4, "rationale": "offline",
        })

    def _three_prompts(self, *, max_abs_bps=None):
        import v22_arms as ARMS
        import v22_runner as RUN
        now = 1_800_000_000_000
        cutoff = now // 3_600_000 * 3_600_000
        candles = [(cutoff - i * 3_600_000,
                    cutoff - i * 3_600_000 + 3_599_999,
                    60_000.0 + i) for i in range(30, 0, -1)]
        market = RUN.frozen_market(candles, cutoff)
        event = {"event_id": "d" * 64, "source": "offline",
                 "cohort": "geopolitics", "published_ms": now - 300_000,
                 "observed_ms": now - 150_000,
                 "title": "offline headline", "summary": ""}
        kwargs = {} if max_abs_bps is None else {
            "max_abs_bps": max_abs_bps}
        return ARMS.build_prompts_3(event, market, **kwargs)

    def test_F07_remaining_actual_tier2_wire_prompts_declare_10000(self):
        prompts = self._three_prompts(max_abs_bps=int(max(
            abs(T2.BPS_RANGE[0]), abs(T2.BPS_RANGE[1]))))
        self.assertEqual(set(prompts), {"news", "news_market", "market"})
        for arm in prompts:
            outbound = TF.build_request("deepseek-v4-flash",
                                        T2.ARM_SYSTEM_T2, prompts[arm])
            user = outbound["messages"][1]["content"]
            self.assertIn("expected_return_bps (-10000..10000)", user)
            self.assertNotIn("(-2000..2000)", user)
        self.assertNotIn("offline headline", prompts["market"])

    def test_F07_remaining_legacy_v22_header_unchanged(self):
        prompts = self._three_prompts()
        self.assertTrue(all("expected_return_bps (-2000..2000)"
                            in text for text in prompts.values()))
        self.assertTrue(all("(-10000..10000)" not in text
                            for text in prompts.values()))

    def test_F07_legacy_v21_parser_still_refuses_9000(self):
        with self.assertRaises(P.PrepError):
            P.parse_arm_response(self.text(9000))

    def test_F07_tier2_parser_accepts_preregistered_9000(self):
        got = P.parse_arm_response(self.text(9000), max_abs_bps=10_000)
        self.assertEqual(got["expected_return_bps"], 9000)
        self.assertEqual(T2.validate_arm_payload(got)["expected_return_bps"], 9000)

    def test_F07_tier2_rejects_10001(self):
        with self.assertRaises(P.PrepError):
            P.parse_arm_response(self.text(10001), max_abs_bps=10_000)

    def test_F07_full_tier2_seal_accepts_9000_bps(self):
        with tempfile.TemporaryDirectory() as d:
            con = T2.open_tier2(Path(d) / "tier2.sqlite")
            self.addCleanup(con.close)
            event = {"event_id": "9" * 64, "cohort": "geopolitics",
                     "published_ms": NOW - 120_000,
                     "observed_ms": NOW - 60_000}
            out = T2.mock_triple(
                con=con, event=event, now_ms=NOW,
                attestation_path=Path(d) / "attestation.jsonl",
                actions={"news": (0.56, 9000, 0.4),
                         "news_market": (0.56, 40, 0.4),
                         "market": (0.50, 0, 0.5)})
            self.assertEqual(out["status"], "sealed")


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload


class ToolSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.att = self.root / "attestation.jsonl"

    def reply(self, extra=None, second=None):
        msg = {"content": '{"p_up":0.5}'}
        msg.update(extra or {})
        choices = [{"message": msg, "finish_reason": "stop"}]
        if second is not None:
            choices.append({"message": second, "finish_reason": "stop"})
        return {
            "model": "deepseek-v4-flash", "id": "fake-id",
            "choices": choices,
        }

    def invoke(self, response):
        return TF.call(
            model="deepseek-v4-flash", system="no tools", user="offline",
            opener=lambda req, timeout=None:
                FakeResponse(json.dumps(response).encode()),
            raw_capture=RAW.raw_capture(
                attestation_path=self.att, trial_id="r4-f09", arm="news"))

    def test_F09_legacy_message_function_call_rejected_and_archived(self):
        body = self.reply({"function_call": {"name": "browser", "arguments": "{}"}})
        with self.assertRaisesRegex(TF.IsolationError,
                                    "legacy_function_call_in_response"):
            self.invoke(body)
        captured = self.root / "raw_model_calls" / "r4-f09" / "news.response.bin"
        self.assertTrue(captured.exists())
        self.assertIn(b'"function_call"', captured.read_bytes())
        facts = AT.response_facts(body)
        self.assertEqual(facts["legacy_function_calls"],
                         ["message.function_call"])

    def test_F09_function_call_in_unselected_choice_rejected(self):
        body = self.reply(second={
            "content": "ignored", "function_call": {"name": "browser"},
        })
        with self.assertRaisesRegex(TF.IsolationError, "legacy_function_call"):
            self.invoke(body)

    def test_F09_top_level_function_call_rejected(self):
        body = self.reply()
        body["function_call"] = {"name": "browser"}
        with self.assertRaisesRegex(TF.IsolationError, "legacy_function_call"):
            self.invoke(body)

    def test_F09_no_tools_normal_reply_still_accepted(self):
        good = self.reply()
        out = self.invoke(good)
        self.assertEqual(out["content"], '{"p_up":0.5}')
        self.assertEqual(AT.response_facts(good)["legacy_function_calls"], [])


if __name__ == "__main__":
    unittest.main()

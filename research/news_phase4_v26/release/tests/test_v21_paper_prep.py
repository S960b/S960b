"""NEWS v2.1 — paper dispatch preparation tests (offline, temp DBs, no model).

Ensures the N/NM prompt pair is built correctly, that market features never
leak past the cutoff, that the opt-in flag and cohort windows are enforced, and
that the model is never called from this layer.
"""
import os
import tempfile
import time
import unittest
from unittest import mock

import v21_dispatch as D
import v21_paper_prep as P

NOW = 1_800_000_000_000
# 26 closed hourly candles ending strictly BEFORE the cutoff.
HOUR = 3_600_000
CLOSES = [100_000 + i for i in range(26)]
# Candle opens are hour-aligned; the last one closed 5 min before NOW.
LAST_OPEN = ((NOW - 5 * 60_000) // HOUR) * HOUR - HOUR
CANDLES = []
for i, price in enumerate(CLOSES):
    open_ms = LAST_OPEN - (25 - i) * HOUR
    CANDLES.append((open_ms, open_ms + HOUR - 1, price))
LAST_CLOSE = CANDLES[-1][1]


def _market():
    return P.market_features(CANDLES, NOW)


MARKET = P.market_features(CANDLES, NOW)


def cutoff_for(now, offset_ms=0):
    return now + offset_ms


def event(**kw):
    e = {"event_id": "a" * 64, "source": "coindesk", "cohort": "context_news",
         "published_ms": NOW - 600_000, "observed_ms": NOW - 540_000,
         "title": "Bitcoin ETF inflows hit a record", "summary": "Spot demand"}
    e.update(kw)
    return e


class PrepTestBase(unittest.TestCase):
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


class PromptTests(PrepTestBase):
    def test_arms_differ_only_by_market_block(self):
        mk = P.market_features(CANDLES, NOW)
        r = P.prepare(None, self.con, event(), enabled=True, now_ms=NOW,
                      market=mk)
        self.assertEqual(r["status"], "ready")
        n, nm = r["prompts"]["news"], r["prompts"]["news_market"]
        self.assertNotEqual(n, nm)
        self.assertNotIn("pre_cutoff_market", n)
        self.assertIn("pre_cutoff_market", nm)
        self.assertIn("Bitcoin ETF inflows hit a record", n)
        self.assertIn("Bitcoin ETF inflows hit a record", nm)

    def test_model_is_never_called(self):
        r = P.prepare(None, self.con, event(), enabled=True, now_ms=NOW,
                      market=P.market_features(CANDLES, NOW))
        self.assertFalse(r["model_called"])
        self.assertEqual(len(r["attempts"]), 2)

    def test_prompts_hash_recorded(self):
        r = P.prepare(None, self.con, event(), enabled=True, now_ms=NOW,
                      market=P.market_features(CANDLES, NOW))
        self.assertEqual(set(r["prompts_sha256"]), {"news", "news_market"})
        self.assertEqual(len(r["prompts_sha256"]["news"]), 64)

    def test_prompt_states_four_hour_horizon(self):
        r = P.prepare(None, self.con, event(), enabled=True, now_ms=NOW,
                      market=P.market_features(CANDLES, NOW))
        self.assertIn("FOUR HOURS", r["prompts"]["news"])


class MarketFeatureTests(unittest.TestCase):
    def test_features_use_only_closed_candles_passed_in(self):
        mk = P.market_features(CANDLES, NOW)
        self.assertAlmostEqual(mk["return_1h"], CLOSES[-1] / CLOSES[-2] - 1)
        self.assertAlmostEqual(mk["return_4h"], CLOSES[-1] / CLOSES[-5] - 1)
        self.assertAlmostEqual(mk["return_24h"], CLOSES[-1] / CLOSES[-25] - 1)
        self.assertGreaterEqual(mk["realized_vol_24h"], 0)
        self.assertEqual(mk["candle_count"], 26)

    def test_candle_closing_after_cutoff_is_rejected(self):
        # A cutoff placed BEFORE the last candle closes must be rejected: the
        # candle's close price would otherwise leak into the prompt.
        with self.assertRaises(P.PrepError) as ctx:
            P.market_features(CANDLES, LAST_CLOSE - 1000)
        self.assertEqual(str(ctx.exception), "insufficient_closed_candles")

    def test_candle_closing_exactly_at_cutoff_is_rejected(self):
        # close_ms == cutoff_ms is NOT "before cutoff" and must still fail.
        with self.assertRaises(P.PrepError):
            P.market_features(CANDLES, LAST_CLOSE)

    def test_cutoff_one_ms_after_close_is_accepted(self):
        mk = P.market_features(CANDLES, LAST_CLOSE + 1)
        self.assertEqual(mk["candle_count"], 26)

    def test_gapped_candles_rejected(self):
        broken = list(CANDLES)
        broken[10] = (broken[10][0] + HOUR, broken[10][1] + HOUR, broken[10][2])
        with self.assertRaises(P.PrepError):
            P.market_features(broken, NOW)

    def test_negative_or_zero_price_rejected(self):
        broken = list(CANDLES)
        broken[-1] = (broken[-1][0], broken[-1][1], 0)
        with self.assertRaises(P.PrepError):
            P.market_features(broken, NOW)

    def test_too_few_candles_rejected(self):
        with self.assertRaises(P.PrepError):
            P.market_features(CANDLES[:10], NOW)


class GuardTests(PrepTestBase):
    def test_opt_in_required(self):
        with self.assertRaises(P.PrepError):
            P.prepare(None, self.con, event(), enabled=False, now_ms=NOW,
                      market=MARKET)

    def test_context_event_older_than_two_hours_rejected(self):
        with self.assertRaises(P.PrepError):
            P.prepare(None, self.con, event(published_ms=NOW - 3 * 3_600_000),
                      enabled=True, now_ms=NOW, market=MARKET)

    def test_fast_event_older_than_five_minutes_rejected(self):
        with self.assertRaises(P.PrepError):
            P.prepare(None, self.con,
                      event(cohort="fast_verified",
                            published_ms=NOW - 600_000),
                      enabled=True, now_ms=NOW, market=MARKET)

    def test_fast_event_within_window_accepted(self):
        r = P.prepare(None, self.con,
                      event(event_id="c" * 64, cohort="fast_verified",
                            published_ms=NOW - 120_000),
                      enabled=True, now_ms=NOW, market=MARKET)
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["cohort"], "fast_verified")

    def test_replay_is_blocked(self):
        ev = event()
        first = P.prepare(None, self.con, ev, enabled=True, now_ms=NOW,
                          market=MARKET)
        self.assertEqual(first["status"], "ready")
        again = P.prepare(None, self.con, ev, enabled=True, now_ms=NOW,
                          market=MARKET)
        self.assertEqual(again["status"], "blocked")
        self.assertEqual(again["reason"], "already_reserved_no_replay")

    def test_daily_pair_cap_enforced(self):
        # The daily pair ceiling is a POLICY number, not a provider limit, so it
        # is pinned here explicitly instead of relying on the default.
        with mock.patch.object(D, "MAX_PAIRS_PER_DAY", 2), \
             mock.patch.object(D, "MAX_CALLS_PER_DAY", 4):
            for i in range(2):
                at = NOW + i * (D.PAIR_LEASE_MS + 1000)
                ev = event(event_id=f"{i:064d}", published_ms=at - 60_000,
                           observed_ms=at - 50_000)
                res = P.prepare(None, self.con, ev, enabled=True, now_ms=at,
                                market=MARKET)
                self.assertEqual(res["status"], "ready", f"pair {i}")
            at3 = NOW + 2 * (D.PAIR_LEASE_MS + 1000)
            third = P.prepare(None, self.con,
                              event(event_id="f" * 64,
                                    published_ms=at3 - 60_000,
                                    observed_ms=at3 - 50_000),
                              enabled=True, now_ms=at3, market=MARKET)
            self.assertEqual(third["status"], "blocked")
            self.assertIn(third["reason"],
                          ("daily_pair_cap", "daily_call_cap"))

    def test_synthetic_reservations_do_not_touch_production_bucket(self):
        # D19: this used to assert calls_today == 0, which only held on a host
        # that happens to carry the legacy v2 database. Without it the counter
        # fail-closes to MAX_CALLS_PER_DAY, which is the CORRECT safety
        # behaviour. The test therefore compares against its own baseline
        # instead of pinning an environment-dependent absolute number.
        before = D.status(self.con, NOW)["production"]["calls_today"]
        P.prepare(None, self.con, event(), enabled=True, now_ms=NOW,
                  market=MARKET)
        after = D.status(self.con, NOW)
        self.assertEqual(after["production"]["calls_today"], before,
                         "a synthetic reservation must not move the "
                         "production bucket")
        self.assertEqual(after["synthetic"]["calls_today"], 2)


class ResponseAndGateTests(unittest.TestCase):
    GOOD = ('{"p_up":0.62,"expected_return_bps":70,'
            '"uncertainty":0.45,"rationale":"hypothesis"}')

    def test_strict_parse_ok(self):
        r = P.parse_arm_response(self.GOOD)
        self.assertEqual(r["p_up"], 0.62)
        self.assertEqual(r["expected_return_bps"], 70.0)

    def test_fenced_response_parsed(self):
        r = P.parse_arm_response("```json\n" + self.GOOD + "\n```")
        self.assertEqual(r["uncertainty"], 0.45)

    def test_extra_key_rejected(self):
        bad = self.GOOD[:-1] + ',"direction":"UP"}'
        with self.assertRaises(P.PrepError):
            P.parse_arm_response(bad)

    def test_out_of_range_rejected(self):
        for body in ('{"p_up":1.5,"expected_return_bps":0,'
                     '"uncertainty":0.5,"rationale":""}',
                     '{"p_up":0.5,"expected_return_bps":5000,'
                     '"uncertainty":0.5,"rationale":""}'):
            with self.subTest(body=body), self.assertRaises(P.PrepError):
                P.parse_arm_response(body)

    def test_garbage_rejected(self):
        with self.assertRaises(P.PrepError):
            P.parse_arm_response("I think it will go up")

    def test_gate_matches_v2_policy(self):
        self.assertEqual(P.decision(0.62, 70), "LONG_PAPER")
        self.assertEqual(P.decision(0.60, 35), "FLAT")     # not > 35
        self.assertEqual(P.decision(0.55, 35.1), "LONG_PAPER")
        self.assertEqual(P.decision(0.45, -36), "SHORT_PAPER")
        self.assertEqual(P.decision(0.44, -35), "FLAT")
        self.assertEqual(P.decision(0.51, 200), "FLAT")

    def test_paper_net_matches_v2_fees(self):
        self.assertAlmostEqual(P.paper_net_bps("LONG_PAPER", 100), 75)
        self.assertAlmostEqual(P.paper_net_bps("SHORT_PAPER", -100), 75)
        self.assertEqual(P.paper_net_bps("FLAT", 100), 0.0)

    def test_quote_validation(self):
        self.assertAlmostEqual(P.parse_quote(
            {"symbol": "BTCUSDT", "price": "74000.50"}), 74000.50)
        for bad in ({"symbol": "ETHUSDT", "price": "1"},
                    {"symbol": "BTCUSDT", "price": "abc"},
                    {"symbol": "BTCUSDT", "price": "1"},
                    {"symbol": "BTCUSDT"}):
            with self.subTest(bad=bad), self.assertRaises(P.PrepError):
                P.parse_quote(bad)


if __name__ == "__main__":
    unittest.main()


class DegeneratePairTests(PrepTestBase):
    def test_missing_market_is_a_hard_fail(self):
        with self.assertRaises(P.PrepError) as ctx:
            P.prepare(None, self.con, event(), enabled=True, now_ms=NOW)
        self.assertEqual(str(ctx.exception), "market_features_required")
        self.assertEqual(D.status(self.con, NOW)["synthetic"]["calls_today"], 0,
                         "no budget may be reserved before a valid pair exists")

    def test_arm_prompts_are_never_identical(self):
        r = P.prepare(None, self.con, event(), enabled=True, now_ms=NOW,
                      market=MARKET)
        digests = set(r["prompts_sha256"].values())
        self.assertEqual(len(digests), 2)
        self.assertNotEqual(r["prompts"]["news"], r["prompts"]["news_market"])
        self.assertIn("ARM:news_only", r["prompts"]["news"])
        self.assertIn("ARM:news_plus_market", r["prompts"]["news_market"])
        self.assertNotIn("pre_cutoff_market", r["prompts"]["news"])
        self.assertIn("pre_cutoff_market", r["prompts"]["news_market"])

    def test_media_source_is_marked_secondary(self):
        r = P.prepare(None, self.con, event(source_kind="media"),
                      enabled=True, now_ms=NOW, market=MARKET)
        self.assertIn('"provenance":"secondary_media"', r["prompts"]["news"])

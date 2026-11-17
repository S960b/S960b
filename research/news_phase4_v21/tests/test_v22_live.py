"""NEWS v2.4 adapters — offline tests with recorded fixtures, no network."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Two layouts: flat next to the modules (live tree) and source/v22_live in the
# published tree. Search for the module instead of assuming where it lives.
for _cand in (HERE, HERE.parent, HERE.parent / "source" / "v22_live",
              HERE / "source" / "v22_live"):
    if (_cand / "v24_adapters.py").is_file():
        sys.path.insert(0, str(_cand))
        break

import v24_adapters as A

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Fed holds rates</title>
<link>https://example.gov/a</link>
<pubDate>Thu, 12 Nov 2026 17:45:00 GMT</pubDate></item>
<item><title>Second release</title>
<link>https://example.gov/b</link>
<pubDate>Fri, 13 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>No link here</title>
<pubDate>Fri, 13 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, body, status=200, ctype="application/rss+xml"):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": ctype}

    def read(self, n=None):
        return self._body if n is None else self._body[:n]


class AdapterTests(unittest.TestCase):
    def opener(self, body, status=200):
        def _open(req, timeout=None):
            return FakeResponse(body, status)
        return _open

    def test_rss_parses_and_drops_incomplete_items(self):
        lim = A.RateLimiter(now=lambda: 0)
        got = A.harvest("fed_press_monetary", limiter=lim,
                        opener=self.opener(RSS))
        # Third item has no link: the adapter drops it rather than inventing one.
        self.assertEqual(len(got["records"]), 2)
        self.assertEqual(got["records"][0]["title"], "Fed holds rates")
        self.assertEqual(got["records"][0]["category"], "monetary_policy")

    def test_every_record_carries_raw_digest_and_terms(self):
        lim = A.RateLimiter(now=lambda: 0)
        got = A.harvest("sec_press", limiter=lim, opener=self.opener(RSS))
        for rec in got["records"]:
            self.assertEqual(len(rec["raw_sha256"]), 64)
            self.assertIn("fair-access", rec["terms_decision"])
            self.assertGreater(rec["observed_ms"], 0)

    def test_403_is_reported_not_retried_around(self):
        lim = A.RateLimiter(now=lambda: 0)

        def _403(req, timeout=None):
            raise urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)

        with self.assertRaises(A.FetchError) as ctx:
            A.harvest("sec_press", limiter=lim, opener=_403)
        self.assertEqual(str(ctx.exception), "http_403")

    def test_oversized_payload_refused(self):
        lim = A.RateLimiter(now=lambda: 0)
        big = b"x" * (A.MAX_BYTES + 10)
        with self.assertRaises(A.FetchError) as ctx:
            A.harvest("fed_press_monetary", limiter=lim,
                      opener=self.opener(big))
        self.assertEqual(str(ctx.exception), "payload_too_large")

    def test_rate_limiter_enforces_sec_interval(self):
        # The limiter books the RESERVED instant (now + wait), so the schedule
        # self-corrects and a burst cannot slip through. Traced behaviour with
        # a 2 s SEC window: (0,0) (500,1500) (3500,500) (5500,500).
        clock = [0]
        lim = A.RateLimiter(now=lambda: clock[0])
        url = "https://www.sec.gov/news/pressreleases.rss"
        self.assertEqual(lim.wait_ms(url), 0)
        clock[0] += 500
        self.assertEqual(lim.wait_ms(url), 1500)
        clock[0] += 3000
        self.assertEqual(lim.wait_ms(url), 500)
        clock[0] += 2000
        self.assertEqual(lim.wait_ms(url), 500)

    def test_non_https_url_refused(self):
        with self.assertRaises(A.FetchError):
            A.fetch("http://example.com/x", limiter=A.RateLimiter(now=lambda: 0))

    def test_quarantined_sources_are_declared_not_silently_dropped(self):
        for key in ("coinbase_status", "bls_web_pages", "sec_web_pages"):
            self.assertIn(key, A.QUARANTINED)
            self.assertTrue(A.QUARANTINED[key])

    def test_allowlist_has_only_verified_hosts(self):
        verified = {"www.federalreserve.gov", "www.sec.gov",
                    "www.whitehouse.gov", "api.kraken.com"}
        for spec in A.SOURCES.values():
            self.assertIn(A._host(spec["url"]), verified)

    def test_user_agent_is_descriptive(self):
        self.assertIn("MultiTool-NEWS-research", A.USER_AGENT)
        self.assertGreater(len(A.USER_AGENT), 20)

    def test_max_attempts_is_bounded(self):
        self.assertLessEqual(A.MAX_ATTEMPTS, 2)


if __name__ == "__main__":
    unittest.main()


class RunnerWiringTests(unittest.TestCase):
    """The standalone runner must seal a triple offline and refuse bad evidence."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="v22run-")
        self.db = Path(self.dir) / "v22.sqlite"
        self.now = 1_800_000_000_000
        self.cutoff = self.now // 3_600_000 * 3_600_000
        self.candles = [(self.cutoff - i * 3_600_000,
                         self.cutoff - i * 3_600_000 + 3_599_999,
                         60_000.0 + i) for i in range(30, 0, -1)]
        self.event = {"event_id": "d" * 64, "source": "dry",
                      "cohort": "context_news",
                      "published_ms": self.now - 600_000,
                      "observed_ms": self.now - 900_000,
                      "title": "dry run", "summary": ""}

    def tearDown(self):
        for suf in ("", "-wal", "-shm"):
            try:
                (Path(str(self.db) + suf)).unlink()
            except FileNotFoundError:
                pass

    def _quote(self, *, after_ms):
        return {"price": 60_000.0, "observed_ms": after_ms + 1_000,
                "source": "dry_run"}

    def _run(self, executor):
        import v22_runner as R
        return R.run_triple(db_path=str(self.db), event=self.event,
                            candles=self.candles, cutoff_ms=self.cutoff,
                            executor=executor, now_ms=self.now,
                            quote_fn=self._quote)

    def test_dry_triple_seals(self):
        import v22_runner as R
        out = self._run(R.mock_executor)
        self.assertEqual(out["status"], "sealed")
        self.assertEqual(set(out["arms"]), {"news", "news_market", "market"})

    def test_second_run_is_idempotent(self):
        import v22_runner as R
        self.assertEqual(self._run(R.mock_executor)["status"], "sealed")
        self.assertEqual(self._run(R.mock_executor)["status"], "already_reserved")

    def test_unknown_tool_evidence_refuses_the_seal(self):
        import v22_runner as R

        def leaky(prompts, *, now_ms):
            res = R.mock_executor(prompts, now_ms=now_ms)
            res[0]["tool_evidence"] = "unknown"
            return res

        # The refusal can come from two legitimate places: the durable attempt
        # state (arm_not_terminal) or the writer's own contract
        # (tool_evidence_not_verified_zero). Both are correct; what matters is
        # that nothing is sealed.
        import v21_paper_prep as P
        with self.assertRaises(P.PrepError) as ctx:
            self._run(leaky)
        self.assertIn("tool_evidence", str(ctx.exception))
        import v22_ledger as L
        con = L.open_v22(str(self.db))
        try:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM v22_triples").fetchone()[0], 0)
        finally:
            con.close()

    def test_market_arm_prompt_carries_no_news(self):
        import v22_runner as R
        prompts = R.mock_executor.__module__ and None
        market = R.frozen_market(self.candles, self.cutoff)
        from v22_arms import build_prompts_3
        built = build_prompts_3(self.event, market)
        self.assertNotIn("dry run", built["market"])
        self.assertNotIn("context_news", built["market"])


class ToolFreeClientTests(unittest.TestCase):
    """The client must be structurally incapable of advertising tools."""

    def setUp(self):
        import v22_toolfree_client as C
        self.C = C

    def test_request_body_has_no_capability_keys(self):
        body = self.C.build_request("m", "sys", "usr")
        for key in ("tools", "tool_choice", "functions", "function_call",
                    "agent", "session", "mcp"):
            self.assertNotIn(key, body)

    def test_guard_rejects_a_capability_key(self):
        # If a future edit smuggles a capability in, the builder must raise.
        original = self.C.FORBIDDEN_REQUEST_KEYS
        self.C.FORBIDDEN_REQUEST_KEYS = tuple(original) + ("temperature",)
        try:
            with self.assertRaises(self.C.IsolationError):
                self.C.build_request("m", "s", "u")
        finally:
            self.C.FORBIDDEN_REQUEST_KEYS = original

    def test_tool_call_in_response_is_refused(self):
        import io
        import json as J

        class Resp:
            status = 200
            headers = {}

            def read(self, n=None):
                return J.dumps({"model": "m", "choices": [{
                    "message": {"content": "x",
                                "tool_calls": [{"id": "1"}]},
                    "finish_reason": "stop"}]}).encode()

        with self.assertRaises(self.C.IsolationError) as ctx:
            self.C.call(model="m", system="s", user="u",
                        opener=lambda req, timeout=None: Resp())
        self.assertIn("tool_call_present", str(ctx.exception))

    def test_missing_model_identity_is_refused(self):
        import json as J

        class Resp:
            status = 200
            headers = {}

            def read(self, n=None):
                return J.dumps({"choices": [{"message": {"content": "ok"},
                                             "finish_reason": "stop"}]}).encode()

        with self.assertRaises(self.C.IsolationError) as ctx:
            self.C.call(model="m", system="s", user="u",
                        opener=lambda req, timeout=None: Resp())
        self.assertIn("model_identity_missing", str(ctx.exception))

    def test_empty_text_is_refused(self):
        import json as J

        class Resp:
            status = 200
            headers = {}

            def read(self, n=None):
                return J.dumps({"model": "m",
                                "choices": [{"message": {"content": "  "},
                                             "finish_reason": "stop"}]}).encode()

        with self.assertRaises(self.C.IsolationError):
            self.C.call(model="m", system="s", user="u",
                        opener=lambda req, timeout=None: Resp())


class CollectorTests(unittest.TestCase):
    """Collector stores only dated items and never invents a timestamp."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="v24c-")
        import v24_collector as COL
        self.COL = COL
        self.con = COL.open_db(Path(self.dir) / "v24.sqlite")

    def tearDown(self):
        self.con.close()

    def test_undated_item_is_not_stored_as_news(self):
        self.assertIsNone(self.COL.parse_published("not a date"))
        self.assertIsNone(self.COL.parse_published(None))

    def test_rfc822_date_parses_to_epoch_ms(self):
        got = self.COL.parse_published("Thu, 12 Nov 2026 17:45:00 GMT")
        self.assertGreater(got, 1_700_000_000_000)

    def test_freshness_report_is_computed_from_published(self):
        self.con.execute(
            "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("a" * 64, "fed", "monetary_policy", "primary", "t",
             "https://x", 1_800_000_000_000, 1_800_000_050_000, "r", "terms",
             1_800_000_050_000))
        self.con.commit()
        rep = self.COL.freshness_report(self.con, now_ms=1_800_000_060_000)
        self.assertEqual(rep[0]["newest_age_s"], 60)


class UndatedSourceTests(unittest.TestCase):
    """A status endpoint must never be stored as a dated news event."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="v24s-")
        import v24_collector as COL
        self.COL = COL
        self.con = COL.open_db(Path(self.dir) / "v24.sqlite")

    def tearDown(self):
        self.con.close()

    def _tick_with(self, records):
        import v24_adapters as AD
        original = AD.harvest

        def fake(sid, *, limiter, opener=None):
            return {"source_id": sid,
                    "fetched": {"status": 200, "content_type": "text/plain",
                                "attempts": 1},
                    "records": records}

        AD.harvest = fake
        try:
            return self.COL.tick(con=self.con, limiter=None, sources=["kraken_status"])
        finally:
            AD.harvest = original

    def _record(self, **over):
        rec = {"source_id": "kraken_status", "category": "crypto",
               "provenance": "official_secondary", "title": "s",
               "origin_url": "https://x", "published_raw": None,
               "observed_ms": 1_800_000_000_000, "raw_sha256": "b" * 64,
               "terms_decision": "terms", "dated": False}
        rec.update(over)
        return rec

    def test_undated_record_goes_to_state_not_events(self):
        out = self._tick_with([self._record()])
        self.assertEqual(out["kraken_status"]["state"], 1)
        self.assertEqual(out["kraken_status"]["new"], 0)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v24_events").fetchone()[0], 0)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v24_state").fetchone()[0], 1)

    def test_state_table_has_no_event_time_column(self):
        cols = [r[1] for r in self.con.execute(
            "PRAGMA table_info(v24_state)").fetchall()]
        self.assertNotIn("published_ms", cols)

    def test_dated_record_still_lands_in_events(self):
        rec = self._record(dated=True,
                           published_raw="Thu, 12 Nov 2026 17:45:00 GMT")
        out = self._tick_with([rec])
        self.assertEqual(out["kraken_status"]["new"], 1)
        self.assertEqual(out["kraken_status"]["state"], 0)

    def test_adapters_mark_kraken_undated(self):
        import v24_adapters as AD
        self.assertIs(AD.SOURCES["kraken_status"]["dated"], False)
        for sid in ("fed_press_monetary", "sec_press", "whitehouse_actions"):
            self.assertIs(AD.SOURCES[sid].get("dated", True), True)

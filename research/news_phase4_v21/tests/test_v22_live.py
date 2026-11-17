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

"""NEWS v2.4 adapters — offline tests with recorded fixtures, no network."""
import io
import json
import os
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


class IsolationProofTests(unittest.TestCase):
    """Hard-fail behaviour when the server or the bridge exposes tool activity."""

    def setUp(self):
        import v22_toolfree_client as TF
        self.TF = TF

    def _resp(self, payload, status=200):
        import json as J

        class R:
            def __init__(self, p):
                self.status = status
                self.headers = {}
                self._p = J.dumps(p).encode()

            def read(self, n=None):
                return self._p

        return lambda req, timeout=None: R(payload)

    def test_tool_call_in_response_is_refused(self):
        with self.assertRaises(self.TF.IsolationError) as ctx:
            self.TF.call(model="m", system="s", user="u", opener=self._resp({
                "model": "m", "choices": [{"message": {
                    "content": "x", "tool_calls": [{"id": "1"}]},
                    "finish_reason": "stop"}]}))
        self.assertIn("tool_call_present_in_response", str(ctx.exception))

    def test_tool_item_in_responses_style_output_is_refused(self):
        with self.assertRaises(self.TF.IsolationError) as ctx:
            self.TF.call(model="m", system="s", user="u", opener=self._resp({
                "model": "m", "output": [{"type": "function_call", "name": "f"}],
                "choices": [{"message": {"content": "x"},
                             "finish_reason": "stop"}]}))
        self.assertIn("tool_item_present_in_response", str(ctx.exception))

    def test_computer_call_item_is_refused(self):
        with self.assertRaises(self.TF.IsolationError):
            self.TF.call(model="m", system="s", user="u", opener=self._resp({
                "model": "m", "output": [{"type": "computer_call"}],
                "choices": [{"message": {"content": "x"},
                             "finish_reason": "stop"}]}))

    def test_empty_message_object_is_refused(self):
        with self.assertRaises(self.TF.IsolationError):
            self.TF.call(model="m", system="s", user="u", opener=self._resp({
                "model": "m", "choices": [{"message": {},
                                           "finish_reason": "stop"}]}))

    def test_bridge_never_adds_tools_to_the_outbound_request(self):
        captured = {}

        class R:
            status = 200
            headers = {}

            def read(self, n=None):
                import json as J
                return J.dumps({"model": "m", "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()

        def opener(req, timeout=None):
            captured["body"] = req.data.decode()
            return R()

        self.TF.call(model="m", system="s", user="u", opener=opener)
        import json as J
        sent = J.loads(captured["body"])
        for key in ("tools", "tool_choice", "functions", "function_call"):
            self.assertNotIn(key, sent)
        self.assertEqual(sorted(sent),
                         ["max_tokens", "messages", "model", "stream",
                          "temperature"])


class CollectorOnlyTests(unittest.TestCase):
    """The unattended loop: singleton, kill switch, crash recovery, ordering."""

    def setUp(self):
        import tempfile
        import collector_only as CO
        self.CO = CO
        self.dir = Path(tempfile.mkdtemp(prefix="v24co-"))
        self.pidfile = self.dir / "pid"
        self.stop = self.dir / "STOP"
        self.log = self.dir / "log"
        self.db = self.dir / "v24.sqlite"

    def tearDown(self):
        for p in (self.pidfile, self.stop):
            if p.exists():
                p.unlink()

    def _args(self, **over):
        base = {"max_hours": 12.0, "interval_s": 1, "db": str(self.db),
                "pidfile": str(self.pidfile), "stop_file": str(self.stop),
                "log": str(self.log), "max_ticks": 1}
        base.update(over)
        return base

    def _lines(self):
        import json
        return [json.loads(x) for x in self.log.read_text().splitlines() if x]

    def test_singleton_refuses_a_second_live_instance(self):
        # A DIFFERENT live process holds the lock; the caller must be refused.
        import subprocess
        other = subprocess.Popen(["sleep", "5"])
        try:
            self.pidfile.write_text(str(other.pid))
            with self.assertRaises(self.CO.SingletonRefused) as ctx:
                self.CO.claim_singleton(self.pidfile)
            self.assertIn("already_running:" + str(other.pid),
                          str(ctx.exception))
        finally:
            other.terminate()
            other.wait()

    def test_stale_pid_is_reclaimed(self):
        self.pidfile.write_text("999999")
        self.assertIsNone(self.CO.live_pid(self.pidfile))
        self.assertEqual(self.CO.claim_singleton(self.pidfile), os.getpid())

    def test_release_does_not_steal_another_runs_lock(self):
        self.pidfile.write_text("999998")
        self.assertFalse(self.CO.release(self.pidfile, os.getpid()))
        self.assertTrue(self.pidfile.exists())

    def test_kill_switch_prevents_any_poll(self):
        self.stop.write_text("stop")
        code = self.CO.main(["--max-hours", "1", "--interval-s", "1",
                             "--db", str(self.db), "--pidfile",
                             str(self.pidfile), "--stop-file", str(self.stop),
                             "--log", str(self.log), "--max-ticks", "5"])
        self.assertEqual(code, self.CO.EXIT_STOP)
        events = [x["event"] for x in self._lines()]
        self.assertIn("stop_file_seen", events)
        self.assertNotIn("tick", events)

    def test_zero_event_poll_is_logged_not_hidden(self):
        import v24_adapters as AD
        saved = AD.harvest

        def empty(sid, *, limiter, opener=None):
            return {"source_id": sid,
                    "fetched": {"status": 200, "content_type": "text/xml",
                                "attempts": 1},
                    "records": []}

        AD.harvest = empty
        try:
            code = self.CO.main(["--max-hours", "1", "--interval-s", "1",
                                 "--db", str(self.db), "--pidfile",
                                 str(self.pidfile), "--stop-file",
                                 str(self.stop), "--log", str(self.log),
                                 "--max-ticks", "2"])
        finally:
            AD.harvest = saved
        self.assertEqual(code, self.CO.EXIT_DEADLINE)
        ticks = [x for x in self._lines() if x["event"] == "tick"]
        self.assertEqual(len(ticks), 2)
        for t in ticks:
            self.assertEqual(t["new_total"], 0)
            self.assertIsNone(t["qualifying"])

    def test_tick_error_does_not_kill_the_loop(self):
        import v24_adapters as AD
        saved = AD.harvest
        calls = {"n": 0}

        def boom(sid, *, limiter, opener=None):
            calls["n"] += 1
            raise RuntimeError("feed exploded")

        AD.harvest = boom
        try:
            code = self.CO.main(["--max-hours", "1", "--interval-s", "1",
                                 "--db", str(self.db), "--pidfile",
                                 str(self.pidfile), "--stop-file",
                                 str(self.stop), "--log", str(self.log),
                                 "--max-ticks", "1"])
        finally:
            AD.harvest = saved
        self.assertEqual(code, self.CO.EXIT_DEADLINE)
        events = [x["event"] for x in self._lines()]
        self.assertIn("tick_error", events)

    def test_pidfile_is_released_on_exit(self):
        self.CO.main(["--max-hours", "1", "--interval-s", "1",
                      "--db", str(self.db), "--pidfile", str(self.pidfile),
                      "--stop-file", str(self.stop), "--log", str(self.log),
                      "--max-ticks", "1"])
        self.assertFalse(self.pidfile.exists())

    def test_start_log_records_deadline_and_mode(self):
        self.CO.main(["--max-hours", "12", "--interval-s", "1",
                      "--db", str(self.db), "--pidfile", str(self.pidfile),
                      "--stop-file", str(self.stop), "--log", str(self.log),
                      "--max-ticks", "1"])
        start = [x for x in self._lines() if x["event"] == "start"][0]
        self.assertEqual(start["mode"], "collector_only")
        self.assertEqual(start["max_hours"], 12.0)
        self.assertGreater(start["deadline_ms"], start["started_ms"])

    def test_collector_only_module_makes_no_model_call(self):
        import collector_only as CO
        import ast
        src = Path(CO.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for banned in ("v22_toolfree_client", "v22_runner", "v22_gate",
                       "openai", "anthropic", "requests"):
            self.assertNotIn(banned, imported)


class FreshnessAtDecisionTimeTests(unittest.TestCase):
    """published_ms and observed_ms must stay distinct, and freshness is
    computed at decision time, not at ingest."""

    def setUp(self):
        import tempfile
        import collector_only as CO
        import v24_collector as COL
        self.CO, self.COL = CO, COL
        self.dir = Path(tempfile.mkdtemp(prefix="v24f-"))
        self.con = COL.open_db(self.dir / "v24.sqlite")

    def tearDown(self):
        self.con.close()

    def _insert(self, digest, category, published_ms, observed_ms):
        self.con.execute(
            "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (digest, "src", category, "primary", "t", "https://x",
             published_ms, observed_ms, "r", "terms", observed_ms))
        self.con.commit()

    def test_stale_item_does_not_qualify(self):
        now = 1_000_000_000_000
        self._insert("a" * 64, "geopolitics", now - 3_600_000, now - 10_000)
        self.assertIsNone(self.CO.freshness(self.con, now))

    def test_fresh_item_qualifies_and_reports_age(self):
        now = 1_000_000_000_000
        self._insert("b" * 64, "geopolitics", now - 120_000, now - 1_000)
        q = self.CO.freshness(self.con, now)
        self.assertEqual(q["age_s"], 120)
        self.assertEqual(q["budget_s"], 600)

    def test_old_observed_time_cannot_rescue_a_stale_publication(self):
        now = 1_000_000_000_000
        self._insert("c" * 64, "geopolitics", now - 10_000_000, now - 5_000)
        self.assertIsNone(self.CO.freshness(self.con, now))

    def test_duplicate_publication_is_stored_once(self):
        now = 1_000_000_000_000
        self._insert("d" * 64, "regulatory", now - 60_000, now - 1_000)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert("d" * 64, "regulatory", now - 60_000, now - 2_000)

    def test_publication_and_observation_are_recorded_separately(self):
        now = 1_000_000_000_000
        self._insert("e" * 64, "regulatory", now - 600_000, now - 1_000)
        pub, obs = self.con.execute(
            "SELECT published_ms, observed_ms FROM v24_events").fetchone()
        self.assertNotEqual(pub, obs)
        self.assertLess(pub, obs)


class ToolEvidenceVocabularyTests(unittest.TestCase):
    """Known_zero must be reserved for server-auditable evidence."""

    def setUp(self):
        import v22_arms as A
        self.A = A

    def test_vocabulary_is_explicit(self):
        for name in ("EV_CLIENT_NO_TOOLS", "EV_GATEWAY_NO_CALLS",
                     "EV_UPSTREAM_UNVERIFIED", "EV_KNOWN_ZERO",
                     "EV_KNOWN_POSITIVE", "EV_UNKNOWN"):
            self.assertTrue(getattr(self.A, name))

    def test_only_known_zero_is_sealable(self):
        self.assertEqual(self.A.SEALABLE_TOOL_EVIDENCE,
                         ("known_zero",))

    def _triple(self, evidence):
        out = []
        for arm in self.A.ARM_NAMES:
            out.append({"arm": arm,
                        "attempt_id": "a" * 64,
                        "session_id": "s",
                        "text": '{"p_up":0.5}',
                        "model": {"providerID": "x", "modelID": "y"},
                        "session_terminal": "completed",
                        "completed_ms": 1,
                        "tool_evidence": evidence,
                        "tool_evidence_provenance": "p",
                        "tools": 0})
        return out

    def test_unverified_arm_blocks_the_triple(self):
        for evidence in ("client_no_tools_advertised",
                         "gateway_response_no_tool_calls",
                         "upstream_unverified", "unknown",
                         "known_positive"):
            with self.assertRaises(Exception) as ctx:
                self.A.validate_three_arms(self._triple(evidence))
            self.assertIn("tool_evidence_not_server_auditable",
                          str(ctx.exception), evidence)

    def test_known_zero_passes(self):
        self.assertTrue(self.A.validate_three_arms(
            self._triple("known_zero")))

    def test_bogus_evidence_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            self.A.validate_three_arms(self._triple("trust me"))
        self.assertIn("tool_evidence_missing", str(ctx.exception))

    def test_mock_harness_reports_unknown(self):
        import v22_ledger as L
        rows = L.mock_arm_results("e" * 64,
                                  {"news": (0.5, 0, 0.5),
                                   "news_market": (0.5, 0, 0.5),
                                   "market": (0.5, 0, 0.5)})
        for row in rows:
            self.assertEqual(row["tool_evidence"], "unknown")
            self.assertNotEqual(row["tool_evidence"], "known_zero")


class GateTests(unittest.TestCase):
    """The v2.2 gate must fail closed and must never lift the legacy pause."""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="v22g-"))
        self.flag = self.dir / "flag"
        self.legacy = self.dir / "PAUSED_MODEL_INFERENCE"
        self.legacy.write_text("PAUSED")
        import v22_gate as G
        self.G = G

    def _authorise(self, purpose="paper_arms"):
        return self.G.authorise(purpose, flag_path=self.flag,
                                legacy_pause_path=self.legacy)

    def test_missing_flag_refuses(self):
        with self.assertRaises(self.G.GateClosed) as ctx:
            self._authorise()
        self.assertIn("no_v22_flag", str(ctx.exception))

    def test_incomplete_flag_refuses(self):
        self.flag.write_text("V22_MODEL_CALLS: AUTHORISED\nowner: kali\n")
        with self.assertRaises(self.G.GateClosed) as ctx:
            self._authorise()
        self.assertIn("flag_incomplete", str(ctx.exception))

    def test_wrong_verb_refuses(self):
        self.G.write_flag(owner="k", purposes=["paper_arms"],
                          authorised_at_utc="t", path=self.flag)
        self.flag.write_text(self.flag.read_text().replace(
            "V22_MODEL_CALLS: AUTHORISED", "V22_MODEL_CALLS: MAYBE"))
        with self.assertRaises(self.G.GateClosed) as ctx:
            self._authorise()
        self.assertIn("flag_not_authorised", str(ctx.exception))

    def test_unlisted_purpose_refuses(self):
        self.G.write_flag(owner="k", purposes=["paper_arms"],
                          authorised_at_utc="t", path=self.flag)
        with self.assertRaises(self.G.GateClosed) as ctx:
            self._authorise("legacy_cycle")
        self.assertIn("purpose_not_authorised", str(ctx.exception))

    def test_removed_legacy_pause_refuses(self):
        self.G.write_flag(owner="k", purposes=["paper_arms"],
                          authorised_at_utc="t", path=self.flag)
        self.legacy.unlink()
        with self.assertRaises(self.G.GateClosed) as ctx:
            self._authorise()
        self.assertIn("legacy_pause_missing", str(ctx.exception))

    def test_valid_flag_authorises_only_named_purpose(self):
        self.G.write_flag(owner="kali", purposes=["paper_arms"],
                          authorised_at_utc="2026-11-17T07:40:00Z",
                          path=self.flag)
        got = self._authorise()
        self.assertEqual(got["owner"], "kali")
        self.assertEqual(oct(self.flag.stat().st_mode & 0o777), "0o600")

    def test_gate_never_deletes_the_legacy_pause(self):
        self.G.write_flag(owner="k", purposes=["paper_arms"],
                          authorised_at_utc="t", path=self.flag)
        self._authorise()
        self.assertTrue(self.legacy.exists())


class LiveExecutorGateTests(unittest.TestCase):
    """The runner's live executor must consult the gate before any request."""

    def setUp(self):
        import tempfile
        import v22_runner as R
        import v22_gate as G
        self.dir = Path(tempfile.mkdtemp(prefix="v22x-"))
        self.R, self.G = R, G
        self.saved = (G.FLAG, G.LEGACY_PAUSE)

    def tearDown(self):
        self.G.FLAG, self.G.LEGACY_PAUSE = self.saved

    def test_executor_refuses_without_flag_and_calls_nothing(self):
        self.G.FLAG = self.dir / "absent"
        self.G.LEGACY_PAUSE = self.dir / "PAUSED_MODEL_INFERENCE"
        self.G.LEGACY_PAUSE.write_text("PAUSED")
        called = []

        def opener(req, timeout=None):
            called.append(req)
            raise AssertionError("network must not be touched")

        ex = self.R.live_executor(opener=opener)
        with self.assertRaises(self.G.GateClosed):
            ex({"news": "p", "news_market": "p", "market": "p"}, now_ms=1)
        self.assertEqual(called, [])

    def test_arm_metadata_records_structural_provenance(self):
        import json as J

        class Resp:
            status = 200
            headers = {}

            def read(self, n=None):
                return J.dumps({"model": "m", "choices": [{
                    "message": {"content": '{"p_up":0.5}'},
                    "finish_reason": "stop"}]}).encode()

        self.G.FLAG = self.dir / "flag"
        self.G.LEGACY_PAUSE = self.dir / "pause"
        self.G.LEGACY_PAUSE.write_text("PAUSED")
        self.G.write_flag(owner="k", purposes=["paper_arms"],
                          authorised_at_utc="t", path=self.G.FLAG)
        out = self.R.live_executor(opener=lambda req, timeout=None: Resp())(
            {"news": "p1", "news_market": "p2", "market": "p3"},
            now_ms=1)
        self.assertEqual(len(out), 3)
        for row in out:
            # Stage-17 correction: a client that omits tools has NOT established
            # server-side isolation, so the arm must not claim known_zero.
            self.assertEqual(row["tool_evidence"], "upstream_unverified")
            self.assertEqual(row["tool_evidence_client"],
                             "client_no_tools_advertised")
            self.assertEqual(row["tool_evidence_gateway"],
                             "gateway_response_no_tool_calls")
            self.assertEqual(row["tool_evidence_upstream"],
                             "upstream_unverified")
            self.assertIn("upstream:upstream_unverified",
                          row["tool_evidence_provenance"])
            self.assertEqual(row["tools"], 0)


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

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
MODULE_DIR = str(Path(A.__file__).resolve().parent)

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


class AttestationTests(unittest.TestCase):
    """The witness must state three surfaces separately and never claim upstream."""

    def setUp(self):
        import tempfile
        import v22_attestation as AT
        self.AT = AT
        self.dir = Path(tempfile.mkdtemp(prefix="v22at-"))
        self.log = self.dir / "attestation.jsonl"

    def _call(self, **over):
        base = {"chat_body": {"model": "m", "messages": [{"role": "user",
                                                         "content": "x"}]},
                "upstream_body": {"model": "m", "max_output_tokens": 32000,
                                  "input": [{"role": "user", "content": "x"}]},
                "parsed": {"id": "resp_1", "model": "m", "status": "completed",
                           "output": [{"type": "message"}]},
                "latency_ms": 120, "model_expected": "m",
                "log": self.log}
        base.update(over)
        return self.AT.attest(**base)

    def test_tool_free_call_records_three_surfaces(self):
        rec = self._call()
        v = rec["verdict"]
        self.assertEqual(v["client"], "client_no_tools_advertised")
        self.assertEqual(v["gateway"], "gateway_response_no_tool_calls")
        self.assertEqual(v["upstream"], "upstream_unverified")

    def test_nested_tool_key_is_caught(self):
        body = {"model": "m", "input": [{"role": "user", "content": "x"}],
                "tools": [{"type": "function"}]}
        self.assertIn("tools", self.AT.tool_surface(body))
        deep = {"a": {"b": [{"function_call": {}}]}}
        self.assertTrue(any("function_call" in h
                            for h in self.AT.tool_surface(deep)))

    def test_response_with_tool_item_is_recorded_not_hidden(self):
        rec = self._call(parsed={"id": "r", "model": "m",
                                 "output": [{"type": "function_call"}]})
        self.assertEqual(rec["verdict"]["gateway"],
                         "gateway_response_has_tool_calls")
        self.assertEqual(rec["response"]["tool_items"], ["function_call"])

    def test_attestation_can_never_write_known_zero(self):
        rec = self._call()
        blob = json.dumps(rec)
        self.assertNotIn("known_zero", blob)
        self.assertIn("upstream_unverified", blob)

    def test_chain_detects_an_edited_line(self):
        self._call()
        self._call()
        ok, n, bad = self.AT.verify_chain(self.log)
        self.assertTrue(ok)
        self.assertEqual(n, 2)
        lines = self.log.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["latency_ms"] = 999
        lines[0] = json.dumps(rec, sort_keys=True)
        self.log.write_text("\n".join(lines) + "\n")
        ok, n, bad = self.AT.verify_chain(self.log)
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    def test_chain_detects_a_deleted_line(self):
        for _ in range(3):
            self._call()
        lines = self.log.read_text().splitlines()
        self.log.write_text("\n".join([lines[0], lines[2]]) + "\n")
        ok, _, _ = self.AT.verify_chain(self.log)
        self.assertFalse(ok)

    def test_egress_digest_changes_when_a_tool_is_forwarded(self):
        a = self._call()["egress"]["sha256"]
        b = self._call(upstream_body={"model": "m", "max_output_tokens": 32000,
                                      "input": [], "tools": [{"type": "function"}]}
                       )["egress"]["sha256"]
        self.assertNotEqual(a, b)

    def test_forwarded_body_mapper_matches_bridge_contract(self):
        # The bridge maps messages -> input and always sends max_output_tokens.
        chat = {"model": "m", "messages": [{"role": "user", "content": "x"}],
                "max_output_tokens": 32000, "temperature": 0.0}
        body = self.AT.forwarded_body(chat)
        self.assertEqual(sorted(body),
                         ["input", "max_output_tokens", "model", "temperature"])
        self.assertNotIn("tools", body)

    def test_forwarded_body_copies_tools_only_when_present(self):
        chat = {"model": "m", "messages": [], "max_output_tokens": 1,
                "tools": [{"type": "function"}]}
        self.assertIn("tools", self.AT.forwarded_body(chat))


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


class Stage21HardeningTests(unittest.TestCase):
    """Adversarial fixtures for the P0 findings of stage 21."""

    def setUp(self):
        import tempfile
        import tier2 as T2
        self.T2 = T2
        self.dir = Path(tempfile.mkdtemp(prefix="s21-"))
        self.now = 1_800_000_000_000

    # --- A: strict parse ------------------------------------------------

    def test_bool_is_not_a_number(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.validate_arm_payload({"p_up": True,
                                          "expected_return_bps": 10,
                                          "uncertainty": 0.5,
                                          "rationale": "x"})
        self.assertIn("not_a_number", str(ctx.exception))

    def test_nan_and_infinity_are_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(self.T2.Tier2Error):
                self.T2.validate_arm_payload({"p_up": bad,
                                              "expected_return_bps": 0,
                                              "uncertainty": 0.5,
                                              "rationale": "x"})

    def test_bounds_are_enforced(self):
        for field, val in (("p_up", 1.5), ("p_up", -0.1),
                           ("uncertainty", 2.0), ("expected_return_bps", 1e9)):
            row = {"p_up": 0.5, "expected_return_bps": 10,
                   "uncertainty": 0.5, "rationale": "x"}
            row[field] = val
            with self.assertRaises(self.T2.Tier2Error):
                self.T2.validate_arm_payload(row)

    def test_duplicate_keys_and_nonstandard_constants_are_refused(self):
        import v21_paper_prep as P
        for bad in ('{"p_up": 0.5, "p_up": 0.9, "expected_return_bps": 1,'
                    ' "uncertainty": 0.5, "rationale": "x"}',
                    '{"p_up": NaN, "expected_return_bps": 1,'
                    ' "uncertainty": 0.5, "rationale": "x"}'):
            with self.assertRaises(Exception):
                P.parse_arm_response(bad)

    def test_frozen_thresholds_not_the_old_half_rule(self):
        # 0.52 was LONG under the old `p > 0.5` rule; it is FLAT now.
        self.assertEqual(self.T2.decision_label(
            {"p_up": 0.52, "expected_return_bps": 40}), "FLAT")
        self.assertEqual(self.T2.decision_label(
            {"p_up": 0.56, "expected_return_bps": 40}), "LONG_PAPER")
        self.assertEqual(self.T2.decision_label(
            {"p_up": 0.44, "expected_return_bps": -40}), "SHORT_PAPER")
        self.assertEqual(self.T2.decision_label(
            {"p_up": 0.55, "expected_return_bps": 35}), "FLAT")

    def test_truncated_response_is_not_terminal(self):
        import v22_toolfree_client as TF
        with self.assertRaises(TF.IsolationError) as ctx:
            TF.call(model="m", system="s", user="u",
                    opener=_resp({"model": "m", "choices": [
                        {"message": {"content": '{"p_up":0.5'},
                         "finish_reason": "length"}]}))
        self.assertIn("non_terminal_finish_reason", str(ctx.exception))

    # --- D: time discipline ---------------------------------------------

    def test_future_publication_refused(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.validate_event({"event_id": "a", "cohort": "geopolitics",
                                    "published_ms": self.now + 60_000}, self.now)
        self.assertIn("published_in_future", str(ctx.exception))

    def test_observed_before_published_refused(self):
        with self.assertRaises(self.T2.Tier2Error):
            self.T2.validate_event({"event_id": "a", "cohort": "geopolitics",
                                    "published_ms": self.now - 10_000,
                                    "observed_ms": self.now - 20_000}, self.now)

    def test_bool_timestamp_refused(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.validate_event({"event_id": "a", "cohort": "geopolitics",
                                    "published_ms": True}, self.now)
        self.assertIn("not_integer_millis", str(ctx.exception))

    def test_transport_lag_budget_enforced(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.validate_event({"event_id": "a", "cohort": "geopolitics",
                                    "published_ms": self.now - 30_000},
                                   self.now, published_max_ms=10_000)
        self.assertIn("transport_lag", str(ctx.exception))

    # --- E: quote and candle --------------------------------------------

    def test_quote_must_be_a_real_price_from_a_known_source(self):
        base = {"observed_ms": self.now, "price": 64_000.0,
                "source": "binance_public_ticker"}
        self.assertTrue(self.T2.validate_quote(dict(base), 1, 1,
                                               now_ms=self.now))
        for bad in ({**base, "price": 0}, {**base, "price": -1},
                    {**base, "price": float("nan")},
                    {**base, "source": "kraken_status"},
                    {**base, "symbol": "ETHUSDT"},
                    {**base, "observed_ms": self.now + 5_000}):
            with self.assertRaises(self.T2.Tier2Error):
                self.T2.validate_quote(bad, 1, 1, now_ms=self.now)

    def test_candle_must_be_aligned_closed_and_positive(self):
        due = 120_050_000
        good = {"closed": True, "open_ms": 120_000_000,
                "close_ms": 120_059_999, "close": 64_000.0}
        self.assertTrue(self.T2.validate_candle(good, due))
        for bad in ({**good, "closed": False},
                    {**good, "open_ms": 120_000_500},
                    {**good, "close_ms": 120_060_000},
                    {**good, "close": 0},
                    {**good, "close": float("inf")},
                    {**good, "symbol": "ETHUSDT"}):
            with self.assertRaises(self.T2.Tier2Error):
                self.T2.validate_candle(bad, due)

    # --- B: activation and budget ---------------------------------------

    def test_durable_call_budget_caps_at_three(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="s21b-"))
        con = self.T2.open_tier2(d / "t.sqlite")
        self.T2.open_attempts(con)
        self.T2.reserve_attempts(con, trial_id="t", now_ms=1)
        for arm in ("news", "news_market", "market"):
            self.T2.record_invocation(con, trial_id="t", arm=arm,
                                      now_ms=1, session_id="s")
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.record_invocation(con, trial_id="t", arm="news",
                                      now_ms=2, session_id="s")
        self.assertIn("budget_exhausted", str(ctx.exception))
        con.close()

    def test_terminal_attempt_is_immutable(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="s21b-"))
        con = self.T2.open_tier2(d / "t.sqlite")
        self.T2.open_attempts(con)
        self.T2.reserve_attempts(con, trial_id="t", now_ms=1)
        self.T2.mark_attempt(con, trial_id="t", arm="news", state="started",
                             now_ms=2)
        self.T2.mark_attempt(con, trial_id="t", arm="news", state="failed",
                             now_ms=3)
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.mark_attempt(con, trial_id="t", arm="news", state="started",
                                 now_ms=4)
        self.assertIn("already_terminal", str(ctx.exception))
        started, completed = con.execute(
            "SELECT started_ms, completed_ms FROM tier2_attempts "
            "WHERE trial_id='t' AND arm='news'").fetchone()
        self.assertEqual((started, completed), (2, 3))
        con.close()

    # --- F: provenance ---------------------------------------------------

    def test_occurrence_ids_separate_sources(self):
        import v24_collector as C
        self.assertNotEqual(C.occurrence_id("fed", "X", 1),
                            C.occurrence_id("sec", "X", 1))
        self.assertEqual(C.semantic_cluster_id("X", 1),
                         C.semantic_cluster_id("X", 1))

    def test_raw_bytes_are_archived_once_and_chmodded(self):
        import tempfile
        import v24_collector as C
        d = Path(tempfile.mkdtemp(prefix="s21f-"))
        f = {"body": b"<rss>x</rss>", "observed_ms": self.now, "status": 200}
        first = C.archive_raw(fetched=f, source_id="fed", raw_dir=d / "raw")
        second = C.archive_raw(fetched=f, source_id="fed", raw_dir=d / "raw")
        self.assertEqual(first["raw_sha256"], second["raw_sha256"])
        files = list((d / "raw").rglob("*.bin"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(files[0].read_bytes(), b"<rss>x</rss>")


def _resp(payload):
    import json as J

    class R:
        status = 200
        headers = {}

        def read(self, n=None):
            return J.dumps(payload).encode()

    return lambda req, timeout=None: R()


class Tier2ProtocolTests(unittest.TestCase):
    """Tier-2 must be separate, weaker, and fail closed. Never a promotion."""

    def setUp(self):
        import tempfile
        import tier2 as T2
        self.T2 = T2
        self.dir = Path(tempfile.mkdtemp(prefix="t2-"))
        self.con = T2.open_tier2(self.dir / "tier2.sqlite")
        self.att = self.dir / "attestation.jsonl"
        self.now = 1_800_000_000_000
        self.event = {"event_id": "e" * 64, "cohort": "geopolitics",
                      "published_ms": self.now - 120_000,
                      "observed_ms": self.now - 60_000}

    def tearDown(self):
        self.con.close()

    def _seal(self, **over):
        kw = {"con": self.con, "event": self.event, "now_ms": self.now,
              "attestation_path": self.att}
        kw.update(over)
        return self.T2.mock_triple(**kw)

    # --- separation from the strict cohort -------------------------------

    def test_tier2_label_is_observational_only(self):
        self.assertEqual(self.T2.TIER, "observational_tier2")
        self.assertNotIn("known_zero", self.T2.TIER)
        self.assertNotEqual(self.T2.PROTOCOL_ID, "prospective-v2.2-arms3")

    def test_tier2_never_writes_known_zero(self):
        out = self._seal()
        self.assertEqual(out["status"], "sealed")
        rows = self.con.execute("SELECT tool_evidence FROM tier2_arms").fetchall()
        self.assertTrue(rows)
        for (evidence,) in rows:
            self.assertEqual(evidence, "observational_tier2")
            self.assertNotEqual(evidence, "known_zero")

    def test_cohort_is_suffixed_and_cannot_be_confused(self):
        self._seal()
        cohort = self.con.execute("SELECT cohort FROM tier2_triples").fetchone()[0]
        self.assertTrue(cohort.endswith("|tier2"))

    def test_schema_pins_the_tier(self):
        with self.assertRaises(Exception):
            self.con.execute(
                "INSERT INTO tier2_triples VALUES(" + ",".join("?" * 20) + ")",
                ("x", "p", "known_zero", "v", "s", "pf", "a", 1, "m", "r", "r",
                 "c", 1, 1, 1.0, 1, 1, 1, "seal", 1))
            self.con.commit()

    def test_counters_are_their_own_namespace(self):
        self._seal()
        c = self.T2.counters(self.con)
        self.assertEqual(c["triples"], 1)
        self.assertIn("never merge", c["note"])

    # --- fail-closed cases -----------------------------------------------

    def test_gate_is_closed_so_no_model_can_be_called(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.require_gate(self.dir / "TIER2_ACTIVE")
        self.assertIn("tier2_not_activated", str(ctx.exception))

    def test_tool_artifact_in_response_refuses(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._seal(tool_artifact=True)
        self.assertIn("tool_artifact", str(ctx.exception))

    def test_missing_terminal_response_refuses(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._seal(terminal=False)
        self.assertIn("terminal_response_missing", str(ctx.exception))

    def test_ambiguous_retry_refuses(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._seal(retried=True)
        self.assertIn("ambiguous_retry", str(ctx.exception))

    def test_wrong_model_refuses(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._seal(wrong_model_arm="news")
        self.assertIn("model_identity_mismatch", str(ctx.exception))

    def test_stale_event_refuses(self):
        old = dict(self.event, published_ms=self.now - 3_600_000,
                   observed_ms=self.now - 3_500_000)
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._seal(event=old)
        self.assertIn("event_too_old", str(ctx.exception))

    def test_synthesized_event_refuses(self):
        bad = dict(self.event, synthesized=True)
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._seal(event=bad)
        self.assertIn("synthesized_or_backdated", str(ctx.exception))

    def test_seal_refuses_when_the_chain_is_empty(self):
        # Deleting the evidence must not be recoverable by sealing again: the
        # seal consumes attestation, it never manufactures it.
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.seal_triple(
                con=self.con, event=self.event,
                attestation_path=self.att, prompts_sha256="0" * 64,
                arm_results=[
                    {"arm": a, "text": '{"p_up":0.5,"expected_return_bps":0,'
                                       '"uncertainty":0.5}',
                     "completed_ms": self.now + 1,
                     "tool_evidence": self.T2.TIER,
                     "attestation_record_sha256": "a" * 64,
                     "model_observed": "m", "model_expected": "m",
                     "terminal_response": True, "retried_ambiguously": False,
                     "tool_artifacts": []}
                    for a in ("news", "news_market", "market")],
                quote={"price": 64_000.0, "observed_ms": self.now + 2,
                       "source": "binance_public_ticker",
                       "symbol": "BTCUSDT"},
                now_ms=self.now + 5_000)
        self.assertIn("attestation_chain_empty", str(ctx.exception))

    def test_tampered_attestation_record_refuses(self):
        self._seal()
        lines = self.att.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["response"]["sha256"] = "9" * 64
        lines[0] = json.dumps(rec, sort_keys=True)
        self.att.write_text("\n".join(lines) + "\n")
        with self.assertRaises(self.T2.Tier2Error):
            self.T2.load_chain(self.att)

    def test_quote_timing_is_enforced(self):
        out = self._seal()
        self.assertEqual(out["status"], "sealed")
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.validate_quote({"price": 1.0, "observed_ms": 1}, 1_000_000)
        self.assertIn("entry_not_after_last_arm", str(ctx.exception))

    def test_candle_must_be_closed_and_in_range(self):
        # Stage-21: the validator now demands a real 1-minute candle, so the
        # fixtures carry an aligned open, a 59 999 ms span and a price.
        open_ms = 960_000
        due = 1_010_000
        good = {"closed": True, "open_ms": open_ms,
                "close_ms": open_ms + 59_999, "close": 64_000.0}
        self.assertTrue(self.T2.validate_candle(good, due))
        for bad in ({"closed": False},
                    {"close_ms": open_ms + 59_000},      # not aligned
                    {"close_ms": open_ms + 60_000},      # wrong span
                    {"close": 0}):
            with self.assertRaises(self.T2.Tier2Error):
                self.T2.validate_candle({**good, **bad}, due)

    # --- pre-registration -------------------------------------------------

    def test_preregistration_exists_and_predates_data(self):
        path = self.T2.PROTOCOL_FILE
        self.assertTrue(path.is_file(), "protocol file missing")
        text = path.read_text(encoding="utf-8")
        self.assertIn("prospective-v2.5-tier2", text)
        self.assertIn("registered_utc", text)
        self.assertIn("ZERO Tier-2 rows exist", text)
        # The strict requirement must still be stated as unmet, not relaxed.
        self.assertIn("known_zero", text)
        self.assertIn("upstream", text.lower())

    def test_protocol_hash_is_recorded_in_every_row(self):
        self._seal()
        stored, expected = self.con.execute(
            "SELECT protocol_file_sha256 FROM tier2_triples").fetchone()[0], \
            self.T2.protocol_sha()
        self.assertEqual(stored, expected)

    def test_preregistration_forbids_upgrade_language(self):
        text = self.T2.PROTOCOL_FILE.read_text(encoding="utf-8")
        banned = ["fully isolated", "provider proof", "certified"]
        # They may appear ONLY inside the forbidden-usage sentence.
        used = text.lower().count("fully isolated")
        self.assertLessEqual(used, 1)

    # --- non-mixing -------------------------------------------------------

    def test_tier2_db_is_a_different_file_from_strict(self):
        strict = Path.home() / ".local/share/news-phase4-v24/v22.sqlite"
        self.assertNotEqual(Path(self.T2.DEFAULT_DB), strict)
        self.assertIn("phase4-v25", str(self.T2.DEFAULT_DB))

    def test_tier2_schema_has_no_prospectustrict_table(self):
        names = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"tier2_triples", "tier2_arms",
                                 "tier2_outcomes"})


class Tier2ActivationTests(unittest.TestCase):
    """The trial gate must be an owner-approved record, not a bare flag file."""

    def setUp(self):
        import tempfile
        import tier2 as T2
        self.T2 = T2
        self.dir = Path(tempfile.mkdtemp(prefix="t2act-"))
        self.gate = self.dir / "TIER2_ACTIVE"
        self.act = self.dir / "TIER2_ACTIVATION.json"

    def _record(self, **over):
        rec = {"trial_id": "trial-1",
               "purpose": self.T2.TRIAL_PURPOSE,
               "owner_seq": 157,
               "owner_task_id": "task_760cfd0ff32f51a5c99ad18ef08613a5",
               "started_utc": "2026-11-17T10:05:00Z",
               "protocol_sha256": self.T2.protocol_sha(),
               "erratum_sha256": __import__("hashlib").sha256(
                   Path(self.T2.ERRATUM_FILE).read_bytes()).hexdigest(),
               "max_model_invocations": 3, "max_triples": 1}
        rec.update(over)
        return rec

    def _write(self, rec):
        self.act.write_text(json.dumps(rec), encoding="utf-8")

    def test_flag_without_activation_record_refuses(self):
        self.gate.write_text("")
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.require_gate(self.gate, self.act)
        self.assertIn("tier2_not_activated", str(ctx.exception))

    def test_activation_without_flag_refuses(self):
        self._write(self._record())
        with self.assertRaises(self.T2.Tier2Error):
            self.T2.require_gate(self.gate, self.act)

    def test_wrong_purpose_refuses(self):
        self.gate.write_text("")
        self._write(self._record(purpose="broad_research"))
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.require_gate(self.gate, self.act)
        self.assertIn("purpose_not_trial", str(ctx.exception))

    def test_more_than_one_triple_refuses(self):
        self.gate.write_text("")
        self._write(self._record(max_triples=5))
        with self.assertRaises(self.T2.Tier2Error):
            self.T2.require_gate(self.gate, self.act)

    def test_more_than_three_invocations_refuses(self):
        self.gate.write_text("")
        self._write(self._record(max_model_invocations=30))
        with self.assertRaises(self.T2.Tier2Error):
            self.T2.require_gate(self.gate, self.act)

    def test_protocol_sha_must_match_bytes_in_use(self):
        self.gate.write_text("")
        self._write(self._record(protocol_sha256="0" * 64))
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.require_gate(self.gate, self.act)
        self.assertIn("protocol_sha_mismatch", str(ctx.exception))

    def test_erratum_sha_must_match(self):
        self.gate.write_text("")
        self._write(self._record(erratum_sha256="0" * 64))
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.require_gate(self.gate, self.act)
        self.assertIn("erratum_sha_mismatch", str(ctx.exception))

    def test_complete_valid_activation_is_accepted(self):
        self.gate.write_text("")
        self._write(self._record())
        rec = self.T2.require_gate(self.gate, self.act)
        self.assertEqual(rec["trial_id"], "trial-1")
        self.assertEqual(rec["owner_seq"], 157)

    def test_attempts_cannot_be_reserved_twice(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="t2att-"))
        con = self.T2.open_tier2(d / "t.sqlite")
        self.T2.open_attempts(con)
        self.T2.reserve_attempts(con, trial_id="trial-1", now_ms=1)
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self.T2.reserve_attempts(con, trial_id="trial-1", now_ms=2)
        self.assertIn("trial_already_reserved", str(ctx.exception))
        con.close()

    def test_reservation_writes_all_three_arms_before_any_call(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="t2att-"))
        con = self.T2.open_tier2(d / "t.sqlite")
        self.T2.open_attempts(con)
        self.T2.reserve_attempts(con, trial_id="trial-2", now_ms=1)
        rows = con.execute("SELECT arm,state FROM tier2_attempts "
                           "WHERE trial_id='trial-2' ORDER BY arm").fetchall()
        self.assertEqual([r[0] for r in rows], sorted(self.T2.A.ARM_NAMES))
        self.assertTrue(all(r[1] == "prepared" for r in rows))
        con.close()

    def test_erratum_records_the_real_commit_time(self):
        text = Path(self.T2.ERRATUM_FILE).read_text(encoding="utf-8")
        self.assertIn("2026-11-17T09:22:39Z", text)
        self.assertIn("2026-11-17T09:52:00Z", text)
        self.assertIn("29 min", text)


class Tier2OrchestratorTests(unittest.TestCase):
    """End-to-end offline harness: event -> gate -> reserve -> calls -> quote ->
    seal -> scheduled label. No network, no model, disposable DB."""

    def setUp(self):
        import tempfile
        import tier2_orchestrator as O
        self.O = O
        self.T2 = O.T2
        self.dir = Path(tempfile.mkdtemp(prefix="orch-"))
        self.con = self.T2.open_tier2(self.dir / "tier2.sqlite")
        self.T2.open_attempts(self.con)      # tables exist before any assertion
        self.att = self.dir / "attestation.jsonl"
        self.now = 1_800_000_000_000
        self.event = {"event_id": "a" * 64, "cohort": "geopolitics",
                      "published_ms": self.now - 120_000,
                      "observed_ms": self.now - 60_000}
        self.prompts = {"news": "p1", "news_market": "p2", "market": "p3"}

    def tearDown(self):
        self.con.close()

    def _gate(self, trial_id="trial-orch"):
        return lambda: {"trial_id": trial_id, "purpose": self.T2.TRIAL_PURPOSE,
                        "owner_seq": 159, "max_model_invocations": 3,
                        "max_triples": 1, "started_utc": "2026-11-17T10:30:00Z",
                        "protocol_sha256": self.T2.protocol_sha(),
                        "erratum_sha256": "e" * 64}

    def _executor(self, *, fail_on=None, artifact_on=None):
        def execute(prompts, *, now_ms, con=None, trial_id=None):
            out = []
            for i, arm in enumerate(("news", "news_market", "market")):
                if arm == fail_on:
                    raise RuntimeError("arm exploded")
                text = ('{"p_up":0.56,"expected_return_bps":40,'
                        '"uncertainty":0.4,"rationale":"orchestrator fixture"}')
                resp_sha = __import__("hashlib").sha256(
                    (arm + text).encode()).hexdigest()
                rec = self.T2._write_attestation(
                    self.att, response_sha=resp_sha, model="m")
                out.append({
                    "arm": arm, "text": text,
                    "completed_ms": now_ms + 100 + i,
                    "tool_evidence": self.T2.TIER,
                    "attestation_record_sha256": rec["record_sha256"],
                    "model_observed": "m", "model_expected": "m",
                    "response_sha256": resp_sha,
                    "request_sha256": "r" * 64,
                    "terminal_response": True, "retried_ambiguously": False,
                    "tool_artifacts": (["function_call"] if arm == artifact_on
                                       else []),
                })
                self.T2.mark_attempt(con, trial_id=trial_id, arm=arm,
                                     state="started", now_ms=now_ms + 50)
                self.T2.mark_attempt(con, trial_id=trial_id, arm=arm,
                                     state="completed", now_ms=now_ms + 150)
            return out
        return execute

    def _quote(self, *, after_ms):
        return {"price": 64_000.0, "observed_ms": after_ms + 5_000,
                "source": "binance_public_ticker", "symbol": "BTCUSDT"}

    def _run(self, **over):
        kw = {"con": self.con, "event": self.event, "prompts": self.prompts,
              "executor": self._executor(), "quote_fn": self._quote,
              "now_ms": self.now + 300_000, "trial_id": "trial-orch",
              "attestation_path": self.att, "gate_check": self._gate(),
              "clock": lambda: self.now + 400_000}
        kw.update(over)
        return self.O.run_trial(**kw)

    def test_full_path_seals_one_observational_row(self):
        out = self._run()
        self.assertEqual(out["status"], "sealed")
        self.assertEqual([s["step"] for s in out["steps"]],
                         ["gate", "event", "reserve", "calls", "quote", "seal",
                          "label"])
        tier = self.con.execute("SELECT tier FROM tier2_triples").fetchone()[0]
        self.assertEqual(tier, "observational_tier2")
        self.assertEqual(self.T2.counters(self.con)["triples"], 1)
        self.assertTrue(out["cohort"].endswith("|tier2"))

    def test_gate_mismatch_stops_before_any_reservation(self):
        with self.assertRaises(self.O.OrchestratorError):
            self._run(gate_check=self._gate(trial_id="other"))
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_attempts").fetchone()[0], 0)

    def test_stale_event_stops_before_reservation(self):
        stale = dict(self.event, published_ms=self.now - 3_600_000,
                     observed_ms=self.now - 3_500_000)
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._run(event=stale)
        self.assertIn("event_too_old", str(ctx.exception))
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_attempts").fetchone()[0], 0)

    def test_arm_failure_leaves_tombstones_and_no_triple(self):
        with self.assertRaises(RuntimeError):
            self._run(executor=self._executor(fail_on="news_market"))
        states = dict(self.con.execute(
            "SELECT arm, state FROM tier2_attempts").fetchall())
        # news completed before the failure; the rest were never started and are
        # closed as failed tombstones by the orchestrator.
        self.assertEqual(states["news"], "completed")
        for arm in ("news_market", "market"):
            self.assertIn(states[arm], ("prepared", "failed"))
        self.assertEqual(self.T2.counters(self.con)["triples"], 0)

    def test_tool_artifact_blocks_the_seal(self):
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._run(executor=self._executor(artifact_on="market"))
        self.assertIn("tool", str(ctx.exception).lower())
        self.assertEqual(self.T2.counters(self.con)["triples"], 0)

    def test_second_run_for_same_trial_is_refused(self):
        self._run()
        with self.assertRaises(self.T2.Tier2Error) as ctx:
            self._run()
        self.assertIn("already_reserved", str(ctx.exception))
        self.assertEqual(self.T2.counters(self.con)["triples"], 1)

    def test_label_is_written_from_a_closed_candle(self):
        out = self._run()
        due = out["due_ms"]
        # D15: the exit is the first CLOSED 1m candle at/after due, so its open
        # is the minute boundary containing (or following) the due moment - not
        # the next one, which would drift past the 60 s allowance.
        open_ms = (due // 60_000) * 60_000
        if open_ms < due:
            open_ms += 60_000
        # The candle must still close within 60 s of due.
        while open_ms + 59_999 - due >= 60_000:
            open_ms -= 60_000
        candle = {"symbol": "BTCUSDT", "closed": True, "open_ms": open_ms,
                  "close_ms": open_ms + 59_999, "close": 64_123.0}
        res = self.O.write_label(con=self.con, event_id="a" * 64, due_ms=due,
                                 candle=candle, now_ms=due + 120_000)
        self.assertEqual(res["label_state"], "labelled")
        self.assertGreaterEqual(res["drift_ms"], 0)
        self.assertLess(res["drift_ms"], 60_000)

    def test_label_refuses_an_unclosed_candle(self):
        out = self._run()
        candle = {"symbol": "BTCUSDT", "closed": False,
                  "open_ms": 1_800_000_000_000, "close_ms": 1_800_000_059_999,
                  "close": 64_000.0}
        with self.assertRaises(self.T2.Tier2Error):
            self.O.write_label(con=self.con, event_id="a" * 64,
                               due_ms=out["due_ms"], candle=candle,
                               now_ms=out["due_ms"] + 60_000)


class Stage22MigrationTests(unittest.TestCase):
    """A migrated database must be idempotent: 70 rows stay 70, not 140."""

    def setUp(self):
        import tempfile
        import sqlite3 as S
        import v24_collector as C
        self.C, self.S = C, S
        self.dir = Path(tempfile.mkdtemp(prefix="mig-"))
        self.db = self.dir / "v24.sqlite"
        self.con = S.connect(str(self.db))
        self.con.executescript(C.SCHEMA)
        # Three legacy rows, keyed the OLD way: sha(title|published), no source.
        for i, (src, title, pub) in enumerate([
                ("fed_press_monetary", "Fed holds rates", 1_700_000_000_000),
                ("sec_press", "SEC proposes rule", 1_700_000_001_000),
                ("whitehouse_actions", "Nominations sent", 1_700_000_002_000)]):
            legacy = __import__("hashlib").sha256(
                f"{title}|{pub}".encode()).hexdigest()
            self.con.execute(
                "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (legacy, src, "geopolitics", "primary", title, "u" + str(i),
                 pub, pub + 5, "r" * 64, "terms", pub))
        self.con.commit()
        self.legacy_ids = [r[0] for r in self.con.execute(
            "SELECT content_sha256 FROM v24_events")]

    def tearDown(self):
        self.con.close()

    def test_migration_is_idempotent_and_preserves_original_ids(self):
        first = self.C.migrate_identity(self.con)
        second = self.C.migrate_identity(self.con)
        self.assertEqual(first["rows"], 3)
        self.assertEqual(second["aliases_added"], 0)
        ids = [r[0] for r in self.con.execute(
            "SELECT content_sha256 FROM v24_events")]
        self.assertEqual(ids, self.legacy_ids, "historical ids were rewritten")

    def test_reinsert_after_migration_creates_no_duplicates(self):
        self.C.migrate_identity(self.con)
        rows = self.con.execute(
            "SELECT source_id, title, published_ms FROM v24_events").fetchall()
        for src, title, pub in rows:
            occ, legacy = self.C.resolve_identity(
                self.con, source_id=src, title=title, published_ms=pub)
            self.assertTrue(legacy)
            try:
                self.con.execute(
                    "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (occ, src, "c", "p", title, "u", pub, pub, "r", "t", pub))
            except self.S.IntegrityError:
                pass
        self.con.commit()
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v24_events").fetchone()[0], 3)

    def test_without_migration_the_same_reinsert_duplicates(self):
        # The failing-before case, kept as a regression guard on the reason.
        rows = self.con.execute(
            "SELECT source_id, title, published_ms FROM v24_events").fetchall()
        added = 0
        for src, title, pub in rows:
            occ = self.C.occurrence_id(src, title, pub)
            try:
                self.con.execute(
                    "INSERT INTO v24_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (occ, src, "c", "p", title, "u", pub, pub, "r", "t", pub))
                added += 1
            except self.S.IntegrityError:
                pass
        self.con.commit()
        self.assertEqual(added, 3, "the duplicate hazard must remain visible")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM v24_events").fetchone()[0], 6)

    def test_alias_links_both_identities(self):
        self.C.migrate_identity(self.con)
        legacy, canonical = self.con.execute(
            "SELECT legacy_key, canonical_id FROM v24_identity_alias "
            "LIMIT 1").fetchone()
        self.assertNotEqual(legacy, canonical)
        hit = self.con.execute(
            "SELECT canonical_id FROM v24_identity_alias WHERE legacy_key=?",
            (legacy,)).fetchone()
        self.assertEqual(hit[0], canonical)


class Stage22RawEvidenceTests(unittest.TestCase):
    """Raw bytes must be archived for every successful response and be readable."""

    def setUp(self):
        import tempfile
        import v24_collector as C
        self.C = C
        self.dir = Path(tempfile.mkdtemp(prefix="raw-"))
        self.con = C.open_db(self.dir / "v24.sqlite")

    def tearDown(self):
        self.con.close()

    def _body(self, payload=b"<rss><channel></channel></rss>"):
        return {"body": payload, "observed_ms": 1_700_000_000_000,
                "status": 200}

    def test_archive_is_readable_back_and_matches_its_digest(self):
        got = self.C.archive_raw(fetched=self._body(), source_id="fed",
                                 raw_dir=self.dir / "raw")
        path = Path(got["raw_path"])
        self.assertTrue(path.is_file())
        data = path.read_bytes()
        self.assertEqual(__import__("hashlib").sha256(data).hexdigest(),
                         got["raw_sha256"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_zero_item_poll_still_archives_the_response(self):
        import v24_adapters as AD
        saved = AD.harvest
        AD.harvest = lambda sid, *, limiter, opener=None: {
            "source_id": sid, "raw_body": self._body(b"<rss/>"),
            "fetched": {"status": 200, "content_type": "text/xml",
                        "attempts": 1}, "records": []}
        try:
            self.C.tick(con=self.con, limiter=None, sources=["fed_press_monetary"])
        finally:
            AD.harvest = saved
        rows = self.con.execute("SELECT raw_sha256, bytes FROM v24_raw_manifest"
                                ).fetchall()
        self.assertEqual(len(rows), 1, "an empty poll must still be provable")
        self.assertEqual(rows[0][1], len(b"<rss/>"))

    def test_manifest_row_points_at_existing_bytes(self):
        import v24_adapters as AD
        saved = AD.harvest
        AD.harvest = lambda sid, *, limiter, opener=None: {
            "source_id": sid, "raw_body": self._body(b"<rss>a</rss>"),
            "fetched": {"status": 200, "content_type": "text/xml",
                        "attempts": 1}, "records": []}
        try:
            self.C.tick(con=self.con, limiter=None, sources=["sec_press"])
        finally:
            AD.harvest = saved
        path, digest = self.con.execute(
            "SELECT path, raw_sha256 FROM v24_raw_manifest").fetchone()
        self.assertTrue(Path(path).is_file())
        self.assertEqual(
            __import__("hashlib").sha256(Path(path).read_bytes()).hexdigest(),
            digest)

    def test_manifest_holds_no_headers_or_credentials(self):
        cols = [r[1] for r in self.con.execute(
            "PRAGMA table_info(v24_raw_manifest)").fetchall()]
        for banned in ("headers", "authorization", "cookie", "token"):
            self.assertNotIn(banned, cols)


class Stage22SubprocessConcurrencyTests(unittest.TestCase):
    """Two real OS processes racing. No barrier file: a missing barrier hangs the
    suite, which it did once - every wait is bounded instead."""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="conc-"))
        self.db = self.dir / "tier2.sqlite"
        import tier2 as T2
        con = T2.open_tier2(self.db)
        T2.open_attempts(con)
        con.close()

    def _run_many(self, script, n):
        import subprocess
        procs = [subprocess.Popen(["/usr/bin/python3", "-c", script],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
                 for _ in range(n)]
        outs = []
        for p in procs:
            try:
                outs.append(p.communicate(timeout=20)[0].strip())
            except subprocess.TimeoutExpired:
                p.kill()
                outs.append("TIMEOUT")
        return outs

    def test_only_one_process_reserves_the_trial(self):
        script = f"""
import sys
sys.path.insert(0, {str(MODULE_DIR)!r})
import tier2 as T2
con = T2.open_tier2({str(self.db)!r})
try:
    T2.reserve_attempts(con, trial_id="race", now_ms=1)
    print("RESERVED")
except T2.Tier2Error as e:
    print("REFUSED", e)
"""
        outs = self._run_many(script, 4)
        self.assertEqual(len([o for o in outs if o.startswith("RESERVED")]), 1,
                         outs)

    def test_call_budget_holds_across_processes(self):
        import tier2 as T2
        con = T2.open_tier2(self.db)
        T2.reserve_attempts(con, trial_id="budget", now_ms=1)
        con.close()
        script = f"""
import sys
sys.path.insert(0, {str(MODULE_DIR)!r})
import tier2 as T2
con = T2.open_tier2({str(self.db)!r})
try:
    print("CALL", T2.record_invocation(con, trial_id="budget",
                                       arm="arm_%s", now_ms=1, session_id="s"))
except T2.Tier2Error as e:
    print("REFUSED", e)
"""
        outs = [self._run_many(script % str(i), 1)[0] for i in range(4)]
        self.assertEqual(len([o for o in outs if o.startswith("CALL")]), 3, outs)

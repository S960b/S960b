"""Stage-28: full path driver -> trigger -> orchestrator with a FAKE executor.

Zero inference. The executor is a plain function; the goal is to prove the chain
seals exactly one three-arm triple, that a second attempt is refused, and that an
ambiguous failure is not retried.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import tier2 as T2
import tier2_trigger as TR
import tier2_orchestrator as O

NOW = 1_800_000_000_000


def fake_executor(att, *, fail_on=None, model="m"):
    """Three-arm answers with real attestation records, no network."""
    def execute(prompts, *, now_ms, con=None, trial_id=None):
        out = []
        for i, arm in enumerate(("news", "news_market", "market")):
            if arm == fail_on:
                raise RuntimeError("arm exploded")
            text = ('{"p_up":0.56,"expected_return_bps":40,'
                    '"uncertainty":0.4,"rationale":"e2e"}')
            resp_sha = hashlib.sha256((arm + text).encode()).hexdigest()
            rec = T2._write_attestation(att, response_sha=resp_sha, model=model)
            out.append({"arm": arm, "text": text,
                        "completed_ms": now_ms + 100 + i,
                        "tool_evidence": T2.TIER,
                        "attestation_record_sha256": rec["record_sha256"],
                        "model_observed": model, "model_expected": model,
                        "response_sha256": resp_sha,
                        "request_sha256": "r" * 64,
                        "terminal_response": True,
                        "retried_ambiguously": False, "tool_artifacts": []})
            T2.mark_attempt(con, trial_id=trial_id, arm=arm, state="started",
                            now_ms=now_ms + 50)
            T2.mark_attempt(con, trial_id=trial_id, arm=arm, state="completed",
                            now_ms=now_ms + 150)
        return out
    return execute


def quote_after(*, after_ms):
    return {"price": 64_000.0, "observed_ms": after_ms + 5_000,
            "source": "binance_public_ticker", "symbol": "BTCUSDT"}


class EndToEndTests(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="e2e-"))
        self.att = self.dir / "attestation.jsonl"
        self.con = T2.open_tier2(self.dir / "tier2.sqlite")
        T2.open_attempts(self.con)
        self.event = {"event_id": "c" * 64, "cohort": "geopolitics",
                      "published_ms": NOW - 120_000,
                      "observed_ms": NOW - 60_000}
        self.prompts = {"news": "p1", "news_market": "p2", "market": "p3"}

    def tearDown(self):
        self.con.close()

    def _gate(self, trial_id="trial-e2e"):
        return lambda: {"trial_id": trial_id, "purpose": T2.TRIAL_PURPOSE,
                        "owner_seq": 175, "max_model_invocations": 3,
                        "max_triples": 1, "started_utc": "2026-11-17T12:00:00Z",
                        "protocol_sha256": T2.protocol_sha(),
                        "erratum_sha256": "e" * 64}

    def _run(self, *, fail_on=None, trial_id="trial-e2e", clock=None):
        return TR.on_new_event(
            con=self.con, event=self.event, prompts=self.prompts,
            executor=fake_executor(self.att, fail_on=fail_on),
            quote_fn=quote_after, now_ms=NOW + 60_000, trial_id=trial_id,
            attestation_path=self.att, gate_check=self._gate(trial_id),
            clock=clock or (lambda: NOW + 400_000))

    def test_full_path_seals_one_three_arm_triple(self):
        out = self._run()
        self.assertEqual(out["status"], "sealed")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_triples").fetchone()[0], 1)
        arms = self.con.execute(
            "SELECT arm FROM tier2_arms ORDER BY arm").fetchall()
        self.assertEqual([a[0] for a in arms],
                         ["market", "news", "news_market"])
        self.assertEqual(self.con.execute(
            "SELECT tier FROM tier2_triples").fetchone()[0],
            "observational_tier2")
        self.assertEqual(TR.claim_state(self.con)["state"], "done")

    def test_second_attempt_is_refused_and_no_second_triple(self):
        self._run()
        with self.assertRaises(T2.Tier2Error) as ctx:
            self._run(trial_id="trial-e2e")
        self.assertIn("claim", str(ctx.exception).lower())
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_triples").fetchone()[0], 1)

    def test_ambiguous_arm_failure_is_not_retried(self):
        with self.assertRaises(T2.Tier2Error):
            self._run(fail_on="news_market")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_triples").fetchone()[0], 0)
        self.assertEqual(TR.claim_state(self.con)["state"], "aborted")
        with self.assertRaises(T2.Tier2Error):
            self._run()
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM tier2_triples").fetchone()[0], 0)

    def test_triple_carries_three_distinct_attestations(self):
        self._run()
        # The attestation reference lives on the triple; the arms carry the
        # canonical tier label and their own text digest.
        ref, pos = self.con.execute(
            "SELECT attestation_record_sha256, attestation_chain_pos "
            "FROM tier2_triples").fetchone()
        chain = T2.load_chain(self.att)
        self.assertIn(ref, chain)
        self.assertEqual(chain[ref][0], pos)
        arms = self.con.execute(
            "SELECT arm, tool_evidence, text_sha256 FROM tier2_arms").fetchall()
        self.assertEqual(len(arms), 3)
        for _arm, evidence, _sha in arms:
            self.assertEqual(evidence, "observational_tier2")
        # Identical fixture text is allowed; what must differ is the evidence
        # record behind each arm. The chain stores one record per call.
        chain = T2.load_chain(self.att)
        self.assertEqual(len(chain), 3, "one attestation record per arm")
        response_digests = {rec["response"]["sha256"]
                            for _pos, rec in chain.values()}
        self.assertEqual(len(response_digests), 3)

    def test_quote_before_last_arm_is_refused(self):
        def early(after_ms):
            return {"price": 64_000.0, "observed_ms": after_ms - 10_000,
                    "source": "binance_public_ticker", "symbol": "BTCUSDT"}

        with self.assertRaises(Exception):
            TR.on_new_event(
                con=self.con, event=self.event, prompts=self.prompts,
                executor=fake_executor(self.att), quote_fn=early,
                now_ms=NOW + 60_000, trial_id="t2", attestation_path=self.att,
                gate_check=self._gate("t2"), clock=lambda: NOW + 400_000)


if __name__ == "__main__":
    unittest.main()

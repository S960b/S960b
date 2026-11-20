"""F03 regression: exact private wire capture survives invalid/partial responses.

No internet, no live trial gate; every fake response comes from a local stub.
"""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "source", ROOT / "source" / "v22_live"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import tier2_raw_store as RAW
import v22_toolfree_client as TF
import tier2 as T2


class FakeResponse:
    status = 200
    headers = {"x-request-id": "provider-fake-123"}

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload


def good_body(idx=0):
    return json.dumps({
        "id": "response-" + str(idx), "model": "deepseek-v4-flash",
        "choices": [{
            "message": {"content": '{"p_up":0.56,'
                        '"expected_return_bps":40,'
                        '"uncertainty":0.4,"rationale":"offline"}'},
            "finish_reason": "stop"
        }]
    }).encode()


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.att = self.root / "attestation.jsonl"
        self.trial = "f03-offline-test"

    def capture(self, arm="news"):
        return RAW.raw_capture(attestation_path=self.att,
                               trial_id=self.trial, arm=arm)

    def test_valid_exact_bytes_and_metadata_are_private(self):
        response = good_body()
        out = TF.call(model="deepseek-v4-flash", system="no web", user="N",
                      opener=lambda request, timeout=None: FakeResponse(response),
                      raw_capture=self.capture())
        found = RAW.verify_wire(
            attestation_path=self.att, trial_id=self.trial, arm="news",
            request_sha256=out["request_sha256"],
            response_sha256=out["response_sha256"])
        self.assertEqual(found["response"], response)
        self.assertEqual(found["request"], out["request_raw"])
        self.assertEqual(out["response_raw"], response)
        base = self.root / "raw_model_calls" / self.trial
        self.assertEqual(base.stat().st_mode & 0o777, 0o700)
        for p in base.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        meta = json.loads((base / "news.response.meta.json").read_text())
        self.assertEqual(meta["http_status"], 200)
        self.assertEqual(meta["provider_request_id"], "provider-fake-123")

    def test_F03_invalid_json_is_still_saved_verbatim(self):
        payload = b'not valid JSON\xff'
        with self.assertRaisesRegex(TF.IsolationError, "response_not_json"):
            TF.call(model="deepseek-v4-flash", system="no web", user="N",
                    opener=lambda request, timeout=None: FakeResponse(payload),
                    raw_capture=self.capture())
        base = self.root / "raw_model_calls" / self.trial
        self.assertEqual((base / "news.response.bin").read_bytes(), payload)
        self.assertTrue((base / "news.request.bin").exists())

    def test_http_error_saves_body_and_request(self):
        payload = b'{"error":"offline rejected"}'
        def fail(_request, timeout=None):
            raise urllib.error.HTTPError(
                "http://local.invalid", 429, "fake", {},
                io.BytesIO(payload))
        with self.assertRaisesRegex(TF.IsolationError, "http_429"):
            TF.call(model="deepseek-v4-flash", system="no web", user="N",
                    opener=fail, raw_capture=self.capture())
        base = self.root / "raw_model_calls" / self.trial
        self.assertEqual((base / "news.response.bin").read_bytes(), payload)
        self.assertEqual(json.loads(
            (base / "news.response.meta.json").read_text())["http_status"], 429)

    def test_transport_failure_saves_request_but_does_not_invent_response(self):
        def offline(_request, timeout=None):
            raise urllib.error.URLError("no network")
        with self.assertRaisesRegex(TF.IsolationError, "transport:"):
            TF.call(model="deepseek-v4-flash", system="no web", user="N",
                    opener=offline, raw_capture=self.capture())
        base = self.root / "raw_model_calls" / self.trial
        self.assertTrue((base / "news.request.bin").is_file())
        self.assertFalse((base / "news.response.bin").exists())

    def test_append_only_files_refuse_overwrite_and_tamper(self):
        out = TF.call(model="deepseek-v4-flash", system="no web", user="N",
                      opener=lambda request, timeout=None: FakeResponse(good_body()),
                      raw_capture=self.capture())
        with self.assertRaises(FileExistsError):
            self.capture()("request", b"replacement", {"status": None})
        p = self.root / "raw_model_calls" / self.trial / "news.response.bin"
        p.write_bytes(b"tampered")
        with self.assertRaisesRegex(RAW.RawEvidenceError, "sha_mismatch"):
            RAW.verify_wire(
                attestation_path=self.att, trial_id=self.trial, arm="news",
                request_sha256=out["request_sha256"],
                response_sha256=out["response_sha256"])

    def test_invalid_path_components_refused(self):
        with self.assertRaisesRegex(RAW.RawEvidenceError, "invalid_trial_id"):
            RAW.raw_capture(attestation_path=self.att, trial_id="../escape", arm="news")
        with self.assertRaisesRegex(RAW.RawEvidenceError, "invalid_arm"):
            RAW.raw_capture(attestation_path=self.att, trial_id=self.trial,
                            arm="../../other")

    def test_F03_real_executor_with_fake_http_persists_all_three_calls(self):
        db = T2.open_tier2(self.root / "tier2.sqlite")
        self.addCleanup(db.close)
        T2.open_attempts(db)
        T2.reserve_attempts(db, trial_id=self.trial, now_ms=1)
        calls = []
        def fake_open(request, timeout=None):
            calls.append(request)
            return FakeResponse(good_body(len(calls)))
        prompts = {"news": "N", "news_market": "NM", "market": "M"}
        activation = {"trial_id": self.trial, "max_model_invocations": 3}
        with patch.object(T2, "require_gate", return_value=activation):
            actual = T2.live_executor(
                model="deepseek-v4-flash", attestation_path=self.att,
                opener=fake_open)(prompts, now_ms=1000, con=db,
                                 trial_id=self.trial)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(actual), 3)
        self.assertEqual(T2.calls_used(db, self.trial), 3)
        for row in actual:
            got = RAW.verify_wire(
                attestation_path=self.att, trial_id=self.trial,
                arm=row["arm"], request_sha256=row["request_sha256"],
                response_sha256=row["response_sha256"])
            self.assertIn(b'"choices"', got["response"])
            self.assertIn(b'"messages"', got["request"])
        self.assertEqual(len(T2.load_chain(self.att)), 3)


if __name__ == "__main__":
    unittest.main()

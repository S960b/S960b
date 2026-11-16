"""NEWS v2.1 — the tool preflight must fail closed.

A cooperative model declining to search is not proof of denial, and a network
error is not a permission refusal: both leave the tool exposed. These tests pin
the GO criterion to positive technical evidence.
"""
import sys
import unittest
from pathlib import Path

# The same file lives at the bundle root (tests/ next to scripts/) and inside
# tests/ in the deployed tree. Search both instead of assuming one layout.
_HERE = Path(__file__).resolve().parent
for _cand in (_HERE / 'scripts', _HERE.parent / 'scripts'):
    if (_cand / 'v21_toolpreflight.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v21_toolpreflight as P

# Fail loudly if the import resolved somewhere unexpected.
assert Path(P.__file__).parent.name == "scripts", P.__file__


def report(*tools):
    return {"tools": list(tools), "texts": []}


class VerdictTests(unittest.TestCase):
    def test_model_self_report_is_not_proof(self):
        # The model merely *says* it was refused: no tool part at all.
        code, reason, _ = P.verdict(report())
        self.assertEqual(code, 4)
        self.assertIn("inconclusive", reason)

    def test_text_refusal_is_not_proof(self):
        # D03: prose that says "permission denied" can just as well come from a
        # proxy, a WAF or an expired key. It is UNSUPPORTED, never GO.
        code, reason, _ = P.verdict(report(
            {"tool": "websearch", "status": "error",
             "error": "Permission denied: tool not allowed in this session"}))
        self.assertEqual(code, 4)
        self.assertIn("unsupported", reason)

    def test_http_403_is_not_proof(self):
        # The original defect: "forbidden" in the body was treated as a boundary.
        for body in ("HTTP Error 403: Forbidden", "403 forbidden",
                     "Forbidden: blocked by proxy"):
            code, reason, _ = P.verdict(report(
                {"tool": "websearch", "status": "error", "error": body}))
            self.assertEqual(code, 4, body)
            self.assertIn("unsupported", reason)

    def test_structured_denied_status_is_proof(self):
        code, reason, detail = P.verdict(report(
            {"tool": "websearch", "status": "denied", "error": ""}))
        self.assertEqual(code, 0)
        self.assertIn("structured", reason)
        self.assertEqual(detail[0]["denial_evidence"], "status:denied")

    def test_structured_error_code_is_proof(self):
        code, reason, _ = P.verdict(report(
            {"tool": "websearch", "status": "error",
             "error": {"type": "permission_denied"}}))
        self.assertEqual(code, 0)
        self.assertIn("structured", reason)

    def test_structured_metadata_flag_is_proof(self):
        code, reason, _ = P.verdict(report(
            {"tool": "websearch", "status": "error", "error": "x",
             "metadata": {"permission_denied": True}}))
        self.assertEqual(code, 0)
        self.assertIn("structured", reason)

    def test_full_state_shape_is_read_not_just_the_flat_one(self):
        code, reason, _ = P.verdict(report(
            {"tool": "websearch",
             "state": {"status": "error",
                       "metadata": {"code": "tool_forbidden"}}}))
        self.assertEqual(code, 0)
        self.assertIn("structured", reason)

    def test_natural_abstention_is_not_isolation(self):
        code, reason, _ = P.verdict(report())
        self.assertEqual(code, 4)
        self.assertIn("abstention", reason)

    def test_network_error_is_not_proof(self):
        code, reason, _ = P.verdict(report(
            {"tool": "websearch", "status": "error",
             "error": "connect ETIMEDOUT"}))
        self.assertEqual(code, 4)
        self.assertIn("unsupported", reason)

    def test_executed_tool_is_a_leak_even_if_also_errors(self):
        code, _, detail = P.verdict(report(
            {"tool": "websearch", "status": "completed", "error": ""},
            {"tool": "memory", "status": "denied", "error": ""}))
        self.assertEqual(code, 3)
        self.assertEqual(detail[0]["tool"], "websearch")

    def test_running_tool_is_a_leak(self):
        code, _, _ = P.verdict(report(
            {"tool": "skill", "status": "running", "error": ""}))
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()

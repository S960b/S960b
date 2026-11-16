"""NEWS v2.1 — the tool preflight must fail closed.

A cooperative model declining to search is not proof of denial, and a network
error is not a permission refusal: both leave the tool exposed. These tests pin
the GO criterion to positive technical evidence.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

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

    def test_permission_refusal_is_proof(self):
        code, reason, _ = P.verdict(report(
            {"tool": "websearch", "status": "error",
             "error": "Permission denied: tool not allowed in this session"}))
        self.assertEqual(code, 0)
        self.assertIn("permission_refused", reason)

    def test_network_error_is_not_proof(self):
        code, reason, _ = P.verdict(report(
            {"tool": "websearch", "status": "error",
             "error": "connect ETIMEDOUT"}))
        self.assertEqual(code, 4)
        self.assertIn("not_a_permission_refusal", reason)

    def test_executed_tool_is_a_leak_even_if_also_errors(self):
        code, _, detail = P.verdict(report(
            {"tool": "websearch", "status": "completed", "error": ""},
            {"tool": "memory", "status": "error",
             "error": "permission denied"}))
        self.assertEqual(code, 3)
        self.assertEqual(detail[0]["tool"], "websearch")

    def test_running_tool_is_a_leak(self):
        code, _, _ = P.verdict(report(
            {"tool": "skill", "status": "running", "error": ""}))
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()

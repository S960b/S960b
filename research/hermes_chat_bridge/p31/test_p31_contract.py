"""Self-contained regression tests for P31 gateway contract helpers."""
import unittest

from p31_contract import (
    ANNOTATIONS, DialogError, READ_ONLY_TOOLS,
    error_from_authority, error_result, tool_annotations,
)


class P31ContractTests(unittest.TestCase):
    def test_read_only_annotations(self):
        for name in READ_ONLY_TOOLS:
            self.assertTrue(tool_annotations(name)["readOnlyHint"])
            self.assertFalse(tool_annotations(name)["destructiveHint"])

    def test_write_annotations(self):
        self.assertTrue(tool_annotations("dialog_append")["idempotentHint"])
        self.assertFalse(tool_annotations("dialog_append")["readOnlyHint"])
        self.assertFalse(tool_annotations("dialog_create")["idempotentHint"])

    def test_defensive_copy(self):
        copy = tool_annotations("dialog_view")
        copy["readOnlyHint"] = False
        self.assertTrue(ANNOTATIONS["dialog_view"]["readOnlyHint"])

    def test_explicit_authority_error_not_masked(self):
        for code in ("wrong_turn", "head_mismatch", "peer_denied", "timeout"):
            self.assertEqual(
                error_from_authority({"code": code, "private": "secret"})["code"],
                code,
            )

    def test_unknown_authority_error_is_redacted(self):
        self.assertEqual(
            error_from_authority({"code": "SECRET!!", "traceback": "private"}),
            {"ok": False, "code": "authority_unavailable"},
        )

    def test_local_errors_are_redacted(self):
        self.assertEqual(
            error_result(RuntimeError("password=secret"), request_id="generated-1"),
            {"ok": False, "code": "internal_error", "request_id": "generated-1"},
        )
        self.assertEqual(error_result(TimeoutError("token=secret"))["code"], "timeout")
        self.assertEqual(error_result(DialogError("wrong_turn"))["code"], "wrong_turn")

    def test_reject_unknown_dialog_code(self):
        with self.assertRaises(ValueError):
            DialogError("secret_error")


if __name__ == "__main__":
    unittest.main()

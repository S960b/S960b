"""R6/F04: HTTP errors preserve status and bounded Retry-After diagnostics.

Uses a fake opener; never touches real network or live SQLite.
"""
import sys
import unittest
import urllib.error
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "source" / "v22_live"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import v24_adapters as AD


class FakeLimiter:
    def wait_ms(self, url):
        return 0


class HTTPErrorDiagnostics(unittest.TestCase):
    def fetch_error(self, status, headers=None):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, status, "test",
                                         headers or {}, None)
        with self.assertRaises(AD.FetchError) as ctx:
            AD.fetch("https://example.com/feed.xml", limiter=FakeLimiter(),
                     opener=opener)
        return str(ctx.exception)

    def test_429_retry_after_preserved(self):
        self.assertEqual(self.fetch_error(429, {"Retry-After": "120"}),
                         "http_429:retry_after=120")

    def test_503_retry_after_http_date_preserved(self):
        self.assertEqual(
            self.fetch_error(503, {"Retry-After": "Wed, 18 Nov 2026 03:00:00 GMT"}),
            "http_503:retry_after=Wed, 18 Nov 2026 03:00:00 GMT")

    def test_403_never_retries_or_adds_retry_after(self):
        self.assertEqual(self.fetch_error(403, {"Retry-After": "120"}), "http_403")

    def test_no_header_same_old_error_code(self):
        self.assertEqual(self.fetch_error(429), "http_429")

    def test_header_cannot_inject_log_line(self):
        result = self.fetch_error(429, {"Retry-After": "10\nFAKE=PASS\r" + "X" * 100})
        self.assertTrue(result.startswith("http_429:retry_after="))
        self.assertNotIn("\n", result)
        self.assertNotIn("\r", result)
        self.assertLessEqual(len(result), len("http_429:retry_after=") + 64)


if __name__ == "__main__":
    unittest.main()

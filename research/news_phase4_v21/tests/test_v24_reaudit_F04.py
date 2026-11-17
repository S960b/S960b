"""F04: one malformed public feed must not abort a polling cycle.

All feeds and HTTP replies are synthetic; no network, no real news DB writes.
"""
import hashlib
import json
import tempfile
import unittest
from email.utils import formatdate
from pathlib import Path
from unittest.mock import patch

import v24_adapters as AD
import v24_collector as COL


class MalformedSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = COL.open_db(self.root / "events.sqlite")
        self.addCleanup(self.db.close)
        self.now = 1_800_000_000_000
        self.invalid = b"<rss><channel><item><title>BAD</title>"
        self.good = (
            "<rss version='2.0'><channel><item><title>SEC official release</title>"
            "<link>https://www.sec.gov/press-release/2026-example</link>"
            "<pubDate>" + formatdate((self.now - 30_000)/1000, usegmt=True)
            + "</pubDate></item></channel></rss>"
        ).encode()
        self.archive_raw = COL.archive_raw

    def fake_fetch(self, url, *, limiter, opener=None, accept=None):
        if "federalreserve.gov" in url:
            body = self.invalid
        elif "kraken.com" in url:
            body = b"{malformed json"
        elif "sec.gov" in url:
            body = self.good
        else:
            raise AssertionError("not a synthetic source: " + url)
        return {"body": body, "url": url, "observed_ms": self.now,
                "status": 200, "content_type": "application/xml",
                "attempts": 1}

    def run_tick(self, sources):
        with patch.object(AD, "fetch", side_effect=self.fake_fetch):
            return COL.tick(con=self.db, limiter=None, sources=sources,
                            raw_dir=self.root / "raw")

    def test_F04_bad_first_rss_archived_and_sec_still_collected(self):
        out = self.run_tick(["fed_press_monetary", "sec_press"])
        self.assertEqual(out["fed_press_monetary"]["error"], "rss_malformed_xml")
        self.assertTrue(out["fed_press_monetary"]["archived"])
        self.assertEqual(out["sec_press"]["new"], 1)
        row = self.db.execute(
            "SELECT path, raw_sha256 FROM v24_raw_manifest "
            "WHERE source_id='fed_press_monetary'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(Path(row[0]).read_bytes(), self.invalid)
        self.assertEqual(row[1], hashlib.sha256(self.invalid).hexdigest())
        errors = self.db.execute(
            "SELECT error FROM v24_fetch_log WHERE source_id='fed_press_monetary'"
        ).fetchall()
        self.assertEqual(errors, [("rss_malformed_xml",)])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM v24_events").fetchone()[0], 1)

    def test_F04_malformed_json_does_not_block_sec(self):
        out = self.run_tick(["kraken_status", "sec_press"])
        self.assertEqual(out["kraken_status"]["error"], "json_malformed_payload")
        self.assertTrue(out["kraken_status"]["archived"])
        self.assertEqual(out["sec_press"]["new"], 1)
        self.assertIsNotNone(self.db.execute(
            "SELECT path FROM v24_raw_manifest "
            "WHERE source_id='kraken_status'").fetchone())

    def test_F04_adapter_reports_parse_error_without_forging_records(self):
        with patch.object(AD, "fetch", side_effect=self.fake_fetch):
            got = AD.harvest("fed_press_monetary", limiter=None)
        self.assertEqual(got["records"], [])
        self.assertEqual(got["parse_error"], "rss_malformed_xml")
        self.assertEqual(got["raw_body"]["body"], self.invalid)
        self.assertEqual(got["fetched"]["status"], 200)

    def test_F04_well_formed_json_array_is_source_error(self):
        def array_fetch(url, *, limiter, opener=None, accept=None):
            return {"body": b"[]", "url": url, "observed_ms": self.now,
                    "status": 200, "content_type": "application/json",
                    "attempts": 1}
        with patch.object(AD, "fetch", side_effect=array_fetch):
            got = AD.harvest("kraken_status", limiter=None)
        self.assertEqual(got["parse_error"], "json_wrong_root")
        self.assertEqual(got["records"], [])
        self.assertEqual(got["raw_body"]["body"], b"[]")

    def test_F04_no_silent_success_on_corrupt_payload(self):
        first = self.run_tick(["fed_press_monetary"])
        self.assertIn("error", first["fed_press_monetary"])
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM v24_events").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM v24_fetch_log "
                            "WHERE error='rss_malformed_xml'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

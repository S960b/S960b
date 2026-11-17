"""NEWS v2.4 conformance kit — recorded fixtures only, no network."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v24_sources.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, HERE):
    if (_cand / 'v24_conformance.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v24_conformance as K
import v24_sources as S


class ConformanceTests(unittest.TestCase):
    def test_all_seven_categories_validate(self):
        out = K.verify_all()
        self.assertEqual(out["refused"], [])
        self.assertEqual(len(out["ok"]), 7)
        self.assertEqual(out["categories_covered"], sorted(S.CATEGORIES))

    def test_registry_covers_every_category(self):
        declared = K.registry().category_sources()
        for category in S.CATEGORIES:
            self.assertTrue(declared[category], category)

    def test_fixtures_round_trip_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = K.write_fixtures(tmp)
            self.assertEqual(len(written), 7)
            loaded = K.load_fixtures(tmp)
            self.assertEqual(sorted(loaded), sorted(K.FIXTURES))
            for category, payload in loaded.items():
                rec = K.record_from_fixture(payload)
                self.assertEqual(rec.category, category)

    def test_missing_field_is_a_named_refusal(self):
        broken = dict(K.FIXTURES["crypto"])
        broken.pop("origin_url")
        with self.assertRaises(S.AdapterError) as ctx:
            K.record_from_fixture(broken)
        self.assertIn("fixture_missing_field:origin_url", str(ctx.exception))

    def test_iso_offset_parsing(self):
        self.assertEqual(K.parse_timestamp("2026-11-17T00:00:00Z"),
                         K.parse_timestamp("2026-11-17T03:00:00+03:00"))

    def test_iso_without_offset_is_refused(self):
        with self.assertRaises(S.AdapterError) as ctx:
            K.parse_timestamp("2026-11-17T00:00:00")
        self.assertIn("timestamp_without_offset", str(ctx.exception))

    def test_bool_timestamp_refused(self):
        with self.assertRaises(S.AdapterError):
            K.parse_timestamp(True)

    def test_stale_fixture_refused_at_a_fixed_clock(self):
        # Validated at "now" far past the fixture's publication: the freshness
        # rule must catch it even though the shape is perfect.
        out = K.verify_all(now_ms=1_800_100_000_000)
        self.assertGreater(len(out["refused"]), 0)
        self.assertTrue(all("too_stale" in r["reason"] or
                            "transport_lag" in r["reason"]
                            for r in out["refused"]))

    def test_kit_has_no_network_calls(self):
        text = (ROOT / "v24_conformance.py").read_text(encoding="utf-8") \
            if (ROOT / "v24_conformance.py").is_file() else \
            (HERE / "v24_conformance.py").read_text(encoding="utf-8")
        # Match executable calls, not the words: the module's own docstring
        # explains that it makes no network call.
        for banned in ("import urllib", "import requests", "import socket",
                       "urlopen(", "requests.get", "socket.socket"):
            self.assertNotIn(banned, text)


class OrderingCollisionTests(unittest.TestCase):
    """P1: an exact key collision must not make the chosen row vary."""

    def test_identical_keys_are_permutation_invariant(self):
        now = 1_800_000_000_000
        a = S.SourceRecord("bin", "crypto", "primary", now - 1_000, now,
                           "Same headline", "https://a/x",
                           S.content_digest("Same headline", now - 1_000))
        b = S.SourceRecord("bin", "crypto", "primary", now - 1_000, now,
                           "Same headline", "https://b/x",
                           S.content_digest("Same headline", now - 1_000))
        first = [r.origin_url for r in S.canonical_order([a, b])]
        second = [r.origin_url for r in S.canonical_order([b, a])]
        self.assertEqual(first, second)
        self.assertEqual(first, ["https://a/x", "https://b/x"])


if __name__ == "__main__":
    unittest.main()

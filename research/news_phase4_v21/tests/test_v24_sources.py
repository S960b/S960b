"""NEWS v2.4 — fixture-only adapter and selector contract. No network."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v24_sources.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, HERE):
    if (_cand / 'v24_sources.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v24_sources as S

NOW = 1_800_000_000_000
SPEC = S.SourceSpec("ofac", "sanctions", "primary", 900)


def record(**over):
    kw = dict(source_id="ofac", category="sanctions", provenance="primary",
              published_ms=NOW - 60_000, observed_ms=NOW - 30_000,
              title="Treasury sanctions two crypto mixers",
              origin_url="https://example.gov/press/1")
    kw.update(over)
    kw["content_sha256"] = S.content_digest(kw["title"], kw["published_ms"])
    return S.SourceRecord(**kw)


class AdapterContractTests(unittest.TestCase):
    def test_valid_record_accepted(self):
        self.assertTrue(S.validate_record(record(), spec=SPEC, now_ms=NOW))

    def test_unknown_category_refused(self):
        rec = record(category="other")
        rec = S.SourceRecord(**{**rec.__dict__,
                                "content_sha256": S.content_digest(
                                    rec.title, rec.published_ms)})
        with self.assertRaises(S.AdapterError) as ctx:
            S.validate_record(rec, spec=SPEC, now_ms=NOW)
        self.assertIn("category", str(ctx.exception))

    def test_observed_before_published_refused(self):
        with self.assertRaises(S.AdapterError) as ctx:
            S.validate_record(record(published_ms=NOW - 10_000,
                                     observed_ms=NOW - 60_000),
                              spec=SPEC, now_ms=NOW)
        self.assertIn("observed_before_published", str(ctx.exception))

    def _with(self, rec, **over):
        """Rebuild a frozen record with changed fields and a matching digest."""
        kw = {k: getattr(rec, k) for k in
              ("source_id", "category", "provenance", "published_ms",
               "observed_ms", "title", "origin_url")}
        kw.update(over)
        if "content_sha256" not in over:
            kw["content_sha256"] = S.content_digest(kw["title"],
                                                    kw["published_ms"])
        return S.SourceRecord(**kw)

    def test_stale_record_refused_per_category(self):
        # 20 minutes of lag; the sanctions budget is 15 minutes (900 s).
        rec = self._with(record(), published_ms=NOW - 1_200_000,
                         observed_ms=NOW - 10_000)
        lag = (rec.observed_ms - rec.published_ms) / 1000.0
        self.assertGreater(lag, S.FRESHNESS_BUDGET_S["sanctions"])
        with self.assertRaises(S.AdapterError) as ctx:
            S.validate_record(rec, spec=SPEC, now_ms=NOW)
        self.assertIn("too_stale", str(ctx.exception))

    def test_sha_mismatch_refused(self):
        bad = self._with(record(), content_sha256="0" * 64)
        with self.assertRaises(S.AdapterError) as ctx:
            S.validate_record(bad, spec=SPEC, now_ms=NOW)
        self.assertIn("content_digest_mismatch", str(ctx.exception))

    def test_bad_url_refused(self):
        with self.assertRaises(S.AdapterError):
            S.validate_record(record(origin_url="ftp://x"), spec=SPEC,
                              now_ms=NOW)

    def test_future_observation_refused(self):
        with self.assertRaises(S.AdapterError):
            S.validate_record(record(observed_ms=NOW + 1), spec=SPEC,
                              now_ms=NOW)


class SelectorTests(unittest.TestCase):
    def _recs(self):
        return [
            record(title="Treasury sanctions two crypto mixers"),
            record(title="Treasury sanctions two crypto mixers"),   # duplicate
            record(category="energy", source_id="eia", provenance="primary",
                   title="Crude inventories fall sharply",
                   published_ms=NOW - 300_000, observed_ms=NOW - 290_000),
            record(title="Bitcoin ETF inflows record",
                   published_ms=NOW - 5_000, observed_ms=NOW - 4_000),
        ]

    def test_duplicates_collapsed(self):
        accepted, rejected = S.select(self._recs(), now_ms=NOW)
        keys = [S.dedupe_key(r) for r in accepted]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("duplicate", [r for _c, r in rejected])

    def test_order_is_deterministic(self):
        first, _ = S.select(self._recs(), now_ms=NOW)
        second, _ = S.select(list(reversed(self._recs())), now_ms=NOW)
        self.assertEqual([r.content_sha256 for r in first],
                         [r.content_sha256 for r in second])

    def test_category_cap_applies(self):
        accepted, rejected = S.select(self._recs(), now_ms=NOW,
                                      per_category_cap=1)
        per = {}
        for r in accepted:
            per[r.category] = per.get(r.category, 0) + 1
        self.assertTrue(all(v <= 1 for v in per.values()))

    def test_stale_is_rejected_not_silently_kept(self):
        old = record(title="Very old sanctions news",
                     published_ms=NOW - 3_600_000,
                     observed_ms=NOW - 3_500_000)
        accepted, rejected = S.select([old], now_ms=NOW)
        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0][1], "stale")

    def test_relevance_is_category_based_not_keyword(self):
        # The whole point of v2.4: an oil story with no BTC keyword is eligible.
        oil = record(category="energy", source_id="eia",
                     title="Crude inventories fall sharply")
        accepted, _ = S.select([oil], now_ms=NOW)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].category, "energy")


class ImmutabilityTests(unittest.TestCase):
    def test_protocol_version_is_distinct(self):
        self.assertEqual(S.PROTOCOL_VERSION, "prospective-v2.4-sources")
        import v22_arms as A
        self.assertNotEqual(A.PROTOCOL_VERSION, S.PROTOCOL_VERSION)

    def test_no_network_imports(self):
        src = (ROOT / "v24_sources.py") if (ROOT / "v24_sources.py").is_file() \
            else (ROOT / "source" / "v24_sources.py")
        if not src.is_file():
            src = HERE / "v24_sources.py"
        text = src.read_text(encoding="utf-8")
        for banned in ("urllib.request.urlopen", "requests.get", "socket."):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()

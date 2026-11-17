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
SPECS = [
    S.SourceSpec("ofac", "sanctions", "primary", 900),
    S.SourceSpec("eia", "energy", "primary", 900),
    S.SourceSpec("bin", "crypto", "primary", 120),
]
REGISTRY = S.SourceRegistry(SPECS)


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
        # Transport lag and decision age are now separate checks; this record
        # violates the transport budget first, and that is the reason reported.
        self.assertIn("transport_lag_too_large", str(ctx.exception))

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
        result = S.select(self._recs(), now_ms=NOW, registry=REGISTRY)
        accepted = result["accepted"]
        keys = [S.dedupe_key(r) for r in accepted]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("duplicate", [r["reason"] for r in result["rejected"]])

    def test_order_is_deterministic(self):
        first = S.select(self._recs(), now_ms=NOW, registry=REGISTRY)
        second = S.select(list(reversed(self._recs())), now_ms=NOW,
                          registry=REGISTRY)
        self.assertEqual([r.content_sha256 for r in first["accepted"]],
                         [r.content_sha256 for r in second["accepted"]])
        # The winner of a duplicate pair must not depend on arrival order.
        self.assertEqual([r["content_sha256"] for r in first["rejected"]],
                         [r["content_sha256"] for r in second["rejected"]])

    def test_category_cap_applies(self):
        result = S.select(self._recs(), now_ms=NOW, registry=REGISTRY,
                          per_category_cap=1)
        accepted, rejected = result["accepted"], result["rejected"]
        per = {}
        for r in accepted:
            per[r.category] = per.get(r.category, 0) + 1
        self.assertTrue(all(v <= 1 for v in per.values()))

    def test_stale_is_rejected_not_silently_kept(self):
        old = record(title="Very old sanctions news",
                     published_ms=NOW - 3_600_000,
                     observed_ms=NOW - 3_500_000)
        result = S.select([old], now_ms=NOW, registry=REGISTRY)
        self.assertEqual(result["accepted"], [])
        reasons = [r["reason"] for r in result["invalid"] + result["rejected"]]
        self.assertTrue(any("stale" in r for r in reasons), reasons)

    def test_relevance_is_category_based_not_keyword(self):
        # The whole point of v2.4: an oil story with no BTC keyword is eligible.
        oil = record(category="energy", source_id="eia",
                     title="Crude inventories fall sharply")
        accepted = S.select([oil], now_ms=NOW, registry=REGISTRY)["accepted"]
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


class CanonicalOrderTests(unittest.TestCase):
    """Stage-6 P0: the winner must not depend on arrival order."""

    def _pool(self):
        base = [
            record(title="Treasury sanctions two crypto mixers",
                   published_ms=NOW - 50_000, observed_ms=NOW - 40_000),
            record(title="Treasury sanctions two crypto mixers",
                   published_ms=NOW - 90_000, observed_ms=NOW - 80_000),
            record(title="Bitcoin breaks新高 level", published_ms=NOW - 10_000,
                   observed_ms=NOW - 5_000),
            record(category="energy", source_id="eia",
                   title="Crude inventories fall sharply",
                   published_ms=NOW - 20_000, observed_ms=NOW - 15_000),
        ]
        return base

    def test_shuffled_input_gives_identical_result(self):
        import random
        pool = self._pool()
        reference = S.select(pool, now_ms=NOW, registry=REGISTRY)
        for seed in (1, 7, 42):
            shuffled = list(pool)
            random.Random(seed).shuffle(shuffled)
            out = S.select(shuffled, now_ms=NOW, registry=REGISTRY)
            self.assertEqual([r.content_sha256 for r in out["accepted"]],
                             [r.content_sha256 for r in reference["accepted"]],
                             f"seed {seed} changed the accepted set")
            self.assertEqual([r["content_sha256"] for r in out["rejected"]],
                             [r["content_sha256"] for r in reference["rejected"]])
            self.assertEqual(out["category_counts"],
                             reference["category_counts"])

    def test_tie_break_includes_source_id(self):
        # Two crypto records, identical publication time: only the source id
        # can separate them, and it must do so the same way every run.
        a = record(source_id="bin", title="Same headline here again",
                   published_ms=NOW - 5_000)
        b = record(source_id="kx", title="Another crypto headline",
                   published_ms=NOW - 5_000)
        order = S.canonical_order([b, a])
        self.assertEqual([r.source_id for r in order], ["bin", "kx"])
        # And category ordering dominates source id across categories.
        c = record(category="energy", source_id="eia",
                   title="Crude inventories fall")
        order2 = S.canonical_order([c, a])
        self.assertEqual([r.source_id for r in order2], ["bin", "eia"])

    def _with_source(self, rec, source_id, category):
        kw = {k: getattr(rec, k) for k in ("published_ms", "observed_ms",
                                           "title", "origin_url",
                                           "content_sha256")}
        return S.SourceRecord(source_id=source_id, category=category,
                              provenance="primary", **kw)


class BoundaryValidationTests(unittest.TestCase):
    """Stage-6 P0: select() must validate, never crash and never trust."""

    def test_unknown_category_is_rejected_not_a_crash(self):
        bad = record(category="bogus")
        out = S.select([bad], now_ms=NOW, registry=REGISTRY)
        self.assertEqual(out["accepted"], [])
        self.assertEqual(out["invalid"][0]["reason"], "unknown_category:bogus")

    def test_unknown_source_is_rejected(self):
        rec = record(source_id="made-up")
        out = S.select([rec], now_ms=NOW, registry=REGISTRY)
        self.assertEqual(out["accepted"], [])
        self.assertEqual(out["invalid"][0]["reason"], "unknown_source:made-up")

    def test_forged_provenance_is_rejected(self):
        rec = record(provenance="media_wire")
        rec = S.SourceRecord(**{**{k: getattr(rec, k) for k in
                                   ("source_id", "category", "published_ms",
                                    "observed_ms", "title", "origin_url",
                                    "content_sha256")},
                                "provenance": "media_wire"})
        out = S.select([rec], now_ms=NOW, registry=REGISTRY)
        self.assertEqual(out["accepted"], [])
        self.assertIn("provenance_mismatch", out["invalid"][0]["reason"])

    def test_bad_digest_is_rejected(self):
        good = record()
        rec = S.SourceRecord(**{**{k: getattr(good, k) for k in
                                   ("source_id", "category", "provenance",
                                    "published_ms", "observed_ms", "title",
                                    "origin_url")},
                                "content_sha256": "0" * 64})
        out = S.select([rec], now_ms=NOW, registry=REGISTRY)
        self.assertEqual(out["accepted"], [])
        self.assertIn("content_digest_mismatch", out["invalid"][0]["reason"])

    def test_observed_after_decision_is_rejected(self):
        rec = record(observed_ms=NOW + 5_000)
        out = S.select([rec], now_ms=NOW, registry=REGISTRY)
        self.assertEqual(out["accepted"], [])
        self.assertIn("observed_after_decision", out["invalid"][0]["reason"])

    def test_misdeclared_spec_is_rejected(self):
        # crypto budget is 120 s; a spec promising 3600 s is mis-declared.
        broken = S.SourceRegistry([S.SourceSpec("bin", "crypto", "primary", 3600)])
        rec = record(title="Fast crypto headline", source_id="bin",
                     category="crypto")
        out = S.select([rec], now_ms=NOW, registry=broken)
        self.assertEqual(out["accepted"], [])
        self.assertIn("misdeclared_spec", out["invalid"][0]["reason"])

    def test_registry_rejects_duplicate_ids_and_bad_specs(self):
        with self.assertRaises(S.AdapterError):
            S.SourceRegistry([SPEC, SPEC])
        with self.assertRaises(S.AdapterError):
            S.SourceRegistry([S.SourceSpec("x", "nope", "primary", 60)])
        with self.assertRaises(S.AdapterError):
            S.SourceRegistry([S.SourceSpec("x", "crypto", "nope", 60)])


class CoverageTests(unittest.TestCase):
    """Stage-6 P0: missing is never zero; uptime uses time buckets."""

    def _reg(self):
        # A coverage grid needs a declared source for EVERY category, otherwise
        # the timeline cannot distinguish "no news" from "not measured".
        spec = [S.SourceSpec("bin", "crypto", "primary", 120),
                S.SourceSpec("eia", "energy", "primary", 900),
                S.SourceSpec("ofac", "sanctions", "primary", 900)]
        spec += [S.SourceSpec(f"{c}-one", c, "primary", 900)
                 for c in S.CATEGORIES
                 if c not in ("crypto", "energy", "sanctions")]
        return S.SourceRegistry(spec)

    def test_complete_timeline_covers_all_seven_categories(self):
        reg = self._reg()
        recs = [record(published_ms=NOW - 30_000)]
        timeline = S.coverage_timeline(recs, start_ms=NOW - 3_600_000,
                                       end_ms=NOW + 1, bucket_ms=3_600_000,
                                       registry=self._reg(), heartbeat_s=60)
        self.assertEqual(len(timeline[0]["categories"]), len(S.CATEGORIES))
        energy = timeline[0]["categories"]["energy"]
        self.assertEqual(energy["status"], "missing")
        self.assertEqual(energy["sources"], 0)

    def test_outage_window_shows_degraded(self):
        reg = S.SourceRegistry([
            S.SourceSpec("bin", "crypto", "primary", 120),
            S.SourceSpec("kx", "crypto", "primary", 120)]
            + [S.SourceSpec(f"{c}-one", c, "primary", 900)
               for c in S.CATEGORIES if c != "crypto"])
        declared = reg.category_sources()
        self.assertEqual(len(declared["crypto"]), 2)
        recs = [record(source_id="bin", category="crypto",
                       published_ms=NOW - 30_000)]
        timeline = S.coverage_timeline(recs, start_ms=NOW - 3_600_000,
                                       end_ms=NOW + 1, bucket_ms=3_600_000,
                                       registry=reg, heartbeat_s=60)
        self.assertEqual(timeline[0]["categories"]["crypto"]["status"],
                         "degraded")
        self.assertAlmostEqual(
            timeline[0]["categories"]["crypto"]["uptime"], 0.5)

    def test_double_counting_is_impossible(self):
        out = S.select(self._recs(), now_ms=NOW, registry=REGISTRY)
        total = (len(out["accepted"]) + len(out["rejected"])
                 + len(out["invalid"]))
        self.assertEqual(total, len(self._recs()))

    @staticmethod
    def _recs():
        return [record(title="Treasury sanctions two crypto mixers"),
                record(title="Treasury sanctions two crypto mixers")]


class DryRunHarnessTests(unittest.TestCase):
    """The harness must be accounting-complete and order-independent."""

    def setUp(self):
        import v24_dryrun as DR
        self.DR = DR

    def test_accounting_balances(self):
        out = self.DR.run()
        acc = out["accounting"]
        self.assertTrue(acc["balances"])
        self.assertEqual(acc["input"], acc["accepted"] + acc["rejected"]
                         + acc["invalid"])

    def test_all_seven_categories_present_when_healthy(self):
        out = self.DR.run()
        self.assertEqual(set(out["category_counts"]), set(S.CATEGORIES))

    def test_faults_are_reported_not_swallowed(self):
        out = self.DR.run()
        reasons = [r["reason"] for r in out["rejected"]]
        self.assertIn("duplicate", reasons)
        invalid = [r["reason"] for r in out["invalid"]]
        self.assertTrue(any("transport_lag" in r for r in invalid))
        self.assertTrue(any("content_digest" in r for r in invalid))

    def test_harness_is_order_independent(self):
        import random
        now, records = self.DR.synthetic_day()
        reg = self.DR.registry()
        base = S.select(records, now_ms=now, registry=reg)
        shuffled = list(records)
        random.Random(11).shuffle(shuffled)
        out = S.select(shuffled, now_ms=now, registry=reg)
        self.assertEqual([r.content_sha256 for r in out["accepted"]],
                         [r.content_sha256 for r in base["accepted"]])


class CoverageGridTests(unittest.TestCase):
    """Stage-7 P0-A: a full outage must still produce buckets marked missing."""

    def _reg(self):
        spec = [S.SourceSpec(f"{c}-one", c, "primary", 900)
                for c in S.CATEGORIES]
        return S.SourceRegistry(spec)

    def test_total_outage_still_yields_buckets(self):
        reg = self._reg()
        timeline = S.coverage_timeline([], start_ms=NOW, end_ms=NOW + 3 * 3_600_000,
                                       bucket_ms=3_600_000, registry=reg,
                                       heartbeat_s=60)
        self.assertEqual(len(timeline), 3, "every expected bucket must exist")
        for row in timeline:
            for category in S.CATEGORIES:
                self.assertEqual(row["categories"][category]["status"], "missing")

    def test_no_heartbeat_means_unknown_not_ok(self):
        reg = self._reg()
        rec = record(source_id="crypto-one", category="crypto")
        timeline = S.coverage_timeline([rec], start_ms=NOW - 3_600_000,
                                       end_ms=NOW + 1, bucket_ms=3_600_000,
                                       registry=reg)
        self.assertEqual(timeline[0]["categories"]["crypto"]["status"], "unknown",
                         "one story is not proof a source is alive")

    def test_healthy_window_is_ok(self):
        reg = self._reg()
        rec = record(source_id="crypto-one", category="crypto")
        timeline = S.coverage_timeline([rec], start_ms=NOW - 3_600_000,
                                       end_ms=NOW + 1, bucket_ms=3_600_000,
                                       registry=reg, heartbeat_s=60)
        self.assertEqual(timeline[0]["categories"]["crypto"]["status"], "ok")

    def test_unmeasured_category_fails_closed(self):
        reg = S.SourceRegistry([S.SourceSpec("bin", "crypto", "primary", 120)])
        with self.assertRaises(S.AdapterError) as ctx:
            S.coverage_timeline([], start_ms=NOW, end_ms=NOW + 1,
                                bucket_ms=3_600_000, registry=reg)
        self.assertIn("category_without_declared_sources", str(ctx.exception))

    def test_bad_grid_arguments_refused(self):
        reg = self._reg()
        for kwargs in ({"start_ms": NOW, "end_ms": NOW},
                       {"start_ms": NOW, "end_ms": NOW - 1},
                       {"start_ms": True, "end_ms": NOW + 1},
                       {"start_ms": NOW, "end_ms": NOW + 1, "bucket_ms": 0}):
            with self.assertRaises(S.AdapterError):
                S.coverage_timeline([], registry=reg, heartbeat_s=60, **kwargs)

    def test_registry_has_no_shared_global_state(self):
        a = S.SourceRegistry([S.SourceSpec("bin", "crypto", "primary", 120)])
        b = S.SourceRegistry([S.SourceSpec("kx", "crypto", "primary", 120)])
        self.assertEqual(a.category_sources()["crypto"], ("bin",))
        self.assertEqual(b.category_sources()["crypto"], ("kx",))


class FreshnessBoundaryTests(unittest.TestCase):
    """Stage-7 P0-C: the source's own promise tightens the category budget."""

    def test_spec_promise_is_enforced(self):
        spec = S.SourceSpec("ofac", "sanctions", "primary", 30)
        rec = record(published_ms=NOW - 300_000, observed_ms=NOW - 200_000)
        with self.assertRaises(S.AdapterError) as ctx:
            S.validate_record(rec, spec=spec, now_ms=NOW)
        self.assertIn("transport_lag_too_large", str(ctx.exception))

    def test_inside_the_tighter_promise_passes(self):
        spec = S.SourceSpec("ofac", "sanctions", "primary", 30)
        rec = record(published_ms=NOW - 20_000, observed_ms=NOW - 10_000)
        self.assertTrue(S.validate_record(rec, spec=spec, now_ms=NOW))

    def test_now_must_be_strict_int(self):
        for bad in (True, "1800000000000", 1.5):
            with self.assertRaises(S.AdapterError):
                S.validate_record(record(), spec=SPEC, now_ms=bad)

    def test_decision_before_publication_refused(self):
        # A decision instant earlier than the record's publication cannot be a
        # valid decision. The observation check fires first here, which is the
        # same refusal class; a future-observed record is refused separately.
        with self.assertRaises(S.AdapterError):
            S.validate_record(record(), spec=SPEC, now_ms=NOW - 1_000_000)
        with self.assertRaises(S.AdapterError) as ctx:
            S.validate_record(record(observed_ms=NOW + 5_000), spec=SPEC,
                              now_ms=NOW)
        self.assertIn("observed_after_decision", str(ctx.exception))


class UrlSafetyTests(unittest.TestCase):
    def test_credentials_refused(self):
        with self.assertRaises(S.AdapterError):
            S.canonical_url("https://user:pass@example.com/x")

    def test_whitespace_refused(self):
        with self.assertRaises(S.AdapterError):
            S.canonical_url("https://example.com/x y")

    def test_bad_scheme_refused(self):
        for bad in ("ftp://example.com/x", "javascript:alert(1)",
                    "data:text/html,x", "example.com/x"):
            with self.assertRaises(S.AdapterError):
                S.canonical_url(bad)

    def test_null_byte_refused(self):
        with self.assertRaises(S.AdapterError):
            S.canonical_url("https://example.com/x%00y")


class DedupeLimitationTests(unittest.TestCase):
    """P1: the title key is crude, and that is measured, not denied."""

    def test_identical_title_different_source_is_reported_as_merge_risk(self):
        a = record(title="Bitcoin ETF inflows record", source_id="bin",
                   published_ms=NOW - 10_000, observed_ms=NOW - 5_000)
        b = record(title="Bitcoin ETF inflows record", source_id="ofac",
                   category="sanctions", published_ms=NOW - 9_000,
                   observed_ms=NOW - 4_000)
        labels = {a.content_sha256: "story-A", b.content_sha256: "story-B"}
        metrics = S.dedupe_metrics([a, b], by_expected=labels)
        self.assertEqual(metrics["false_merges"], 1,
                         "two distinct stories collapsed into one key")
        self.assertIn("non-semantic", metrics["note"])

    def test_unicode_normalisation_collisions_are_visible(self):
        a = record(title="Bitcoin ETF  inflows", published_ms=NOW - 10_000,
                   observed_ms=NOW - 5_000)
        b = record(title="Bitcoin ETF inflows", published_ms=NOW - 10_000,
                   observed_ms=NOW - 5_000)
        self.assertEqual(S.dedupe_key(a), S.dedupe_key(b))

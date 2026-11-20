"""NEWS v2.1 — issuer provenance taxonomy tests (offline, no network).

Proves that source_kind reflects WHO issued an item, not how it was
transported, and that the fast gate stays conservative: media RSS can never be
fast, and periodic BLS releases can never look like live announcements.
"""
import unittest

from v21_normalize import (ISSUER_INCIDENT_STATUS, ISSUER_PRIMARY,
                           ISSUER_SECONDARY_MEDIA, ISSUER_VERIFIED_SOCIAL,
                           clock_quality_for, eligible_fast, rss_documents,
                           source_kind_for)

NOW = 1_800_000_000_000
FRESH = NOW - 60_000          # one minute old


class ProvenanceTests(unittest.TestCase):
    def test_media_feeds_are_secondary_regardless_of_transport(self):
        for src in ISSUER_SECONDARY_MEDIA:
            with self.subTest(src=src):
                self.assertEqual(source_kind_for(src), "secondary")

    def test_official_issuers_are_primary_over_http(self):
        for src in ("fed", "sec", "whitehouse", "binance"):
            with self.subTest(src=src):
                self.assertEqual(source_kind_for(src), "primary")

    def test_incident_status_feeds_are_primary(self):
        for src in ISSUER_INCIDENT_STATUS:
            with self.subTest(src=src):
                self.assertEqual(source_kind_for(src), "primary")

    def test_bluesky_is_verified_social(self):
        for src in ISSUER_VERIFIED_SOCIAL:
            with self.subTest(src=src):
                self.assertEqual(source_kind_for(src), "verified_social")

    def test_unknown_source_never_silently_becomes_secondary(self):
        # The guard exists so a typo cannot smuggle an unvetted feed in.
        from v21_normalize import only_allowlisted_sources
        self.assertFalse(only_allowlisted_sources("random_blog"))
        self.assertTrue(only_allowlisted_sources("fed"))
        self.assertTrue(only_allowlisted_sources("coindesk"))


class ClockQualityTests(unittest.TestCase):
    def test_official_issuers_stamp_their_own_publication(self):
        for src in ("fed", "sec", "whitehouse", "binance"):
            with self.subTest(src=src):
                self.assertEqual(clock_quality_for(src), "publisher")

    def test_bls_is_periodic_not_publisher(self):
        self.assertEqual(clock_quality_for("bls"), "periodic")

    def test_media_feeds_have_no_publisher_clock(self):
        for src in ISSUER_SECONDARY_MEDIA:
            with self.subTest(src=src):
                self.assertEqual(clock_quality_for(src), "network_received")


class FastGateConservatismTests(unittest.TestCase):
    def _event(self, **kw):
        e = {"published_ms": FRESH, "observed_ms": NOW,
             "source_kind": "primary", "clock_quality": "publisher"}
        e.update(kw)
        return e

    def test_fed_item_can_be_fast_even_over_rss(self):
        e = self._event(source_kind=source_kind_for("fed"),
                        clock_quality=clock_quality_for("fed"))
        self.assertTrue(eligible_fast(e, NOW))

    def test_media_item_is_never_fast_even_when_one_minute_old(self):
        e = self._event(source_kind=source_kind_for("coindesk"),
                        clock_quality=clock_quality_for("coindesk"))
        self.assertFalse(eligible_fast(e, NOW),
                         "media RSS must never be promoted to fast")

    def test_bls_periodic_release_is_never_fast(self):
        e = self._event(source_kind=source_kind_for("bls"),
                        clock_quality=clock_quality_for("bls"))
        self.assertFalse(eligible_fast(e, NOW),
                         "periodic BLS data must never look like a live event")

    def test_old_primary_item_is_still_not_fast(self):
        e = self._event(published_ms=NOW - 20 * 60_000,
                        source_kind=source_kind_for("sec"),
                        clock_quality=clock_quality_for("sec"))
        self.assertFalse(eligible_fast(e, NOW))

    def test_rss_parse_applies_taxonomy(self):
        body = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
                b'<item><title>Federal Reserve issues FOMC statement on rates</title>'
                b'<link>https://example.test/1</link>'
                b'<description>Federal Reserve monetary policy statement</description>'
                b'<pubDate>Mon, 16 Nov 2026 02:00:00 +0000</pubDate></item>'
                b'</channel></rss>')
        for src, want in (("fed", "primary"), ("coindesk", "secondary")):
            with self.subTest(src=src):
                docs = rss_documents(body, NOW, src)
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["source_kind"], want)
                self.assertEqual(docs[0]["clock_quality"],
                                 clock_quality_for(src))


if __name__ == "__main__":
    unittest.main()

"""NEWS v2.1 — freshness / staleness gate tests (offline, no network).

Audits the reported discrepancy: 14 events accepted, 0 fast_eligible.
Proves that stale articles can never reach the fast trigger queue, that
first_received is never substituted for publish time, and that malformed or
duplicated input is rejected rather than silently classified.
"""
import time
import unittest

from v21_normalize import (IntakeError, MAX_AGE_FAST_MS, eligible_fast,
                           rss_documents, statuspage_incidents)

NOW = 1_800_000_000_000
RSS_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>{title}</title><link>https://example.test/{slug}</link>
<description>{summary}</description><pubDate>{pub}</pubDate></item>
</channel></rss>"""


def rss(pub_dt, title="Bitcoin ETF outflows surge to a new record level",
        summary="Bitcoin market reacts to ETF flows", slug="a"):
    return RSS_TMPL.format(title=title, summary=summary, slug=slug, pub=pub_dt)


def dt(ms):
    return time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(ms / 1000))


def as_primary(doc):
    """RSS media feeds are source_kind='secondary' by design.

    Promote a copy to 'primary' so the fast-gate itself can be tested
    independently of the source classification.
    """
    d = dict(doc)
    d["source_kind"] = "primary"
    return d


class StalenessTests(unittest.TestCase):
    def test_fresh_two_minute_item_is_fast_eligible(self):
        raw = rss(dt(NOW - 120_000)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertEqual(len(docs), 1)
        self.assertTrue(eligible_fast(as_primary(docs[0]), NOW))

    def test_six_minute_item_is_not_fast_eligible(self):
        raw = rss(dt(NOW - 360_000)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertEqual(len(docs), 1)
        self.assertFalse(eligible_fast(as_primary(docs[0]), NOW))

    def test_boundary_exactly_five_minutes_is_accepted(self):
        raw = rss(dt(NOW - MAX_AGE_FAST_MS)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertTrue(eligible_fast(as_primary(docs[0]), NOW))

    def test_boundary_one_ms_past_five_minutes_is_rejected(self):
        raw = rss(dt(NOW - MAX_AGE_FAST_MS - 1)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertFalse(eligible_fast(as_primary(docs[0]), NOW))

    def test_ninety_minute_article_never_reaches_fast_queue(self):
        # mirrors the real observed minimum age of 92 minutes
        raw = rss(dt(NOW - 5_523_000)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertEqual(len(docs), 1, "old article is still stored as context")
        self.assertFalse(eligible_fast(as_primary(docs[0]), NOW),
                         "old article must NOT enter the fast trigger queue")

    def test_received_time_is_not_used_as_publish_time(self):
        # A very old article observed now must never look fresh.
        raw = rss(dt(NOW - 22 * 3600_000)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertFalse(eligible_fast(as_primary(docs[0]), NOW))
        self.assertLess(docs[0]["published_ms"], docs[0]["observed_ms"])

    def test_clock_quality_must_be_publisher_or_network(self):
        e = {"published_ms": NOW - 60_000, "observed_ms": NOW,
             "source_kind": "primary", "clock_quality": "assumed"}
        self.assertFalse(eligible_fast(e, NOW))

    def test_source_kind_must_be_primary_or_verified_social(self):
        e = {"published_ms": NOW - 60_000, "observed_ms": NOW,
             "source_kind": "aggregator", "clock_quality": "publisher"}
        self.assertFalse(eligible_fast(e, NOW))


class PublishTimestampTests(unittest.TestCase):
    def test_future_publish_time_is_rejected(self):
        raw = rss(dt(NOW + 3600_000)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertEqual(docs, [], "future dated item must be dropped")

    def test_missing_publish_date_is_rejected(self):
        raw = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
               b'<item><title>Bitcoin ETF outflows surge to a new record level</title>'
               b'<link>https://example.test/x</link>'
               b'<description>Bitcoin market reacts</description></item>'
               b'</channel></rss>')
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertEqual(docs, [], "item without a publish date must be dropped")

    def test_observed_time_is_ours_not_publishers(self):
        raw = rss(dt(NOW - 120_000)).encode()
        docs = rss_documents(raw, NOW, "coindesk")
        self.assertEqual(docs[0]["observed_ms"], NOW)
        self.assertEqual(docs[0]["published_ms"], (NOW - 120_000) // 1000 * 1000)


class MalformedInputTests(unittest.TestCase):
    def test_broken_xml_raises_controlled_error(self):
        with self.assertRaises(IntakeError):
            rss_documents(b"<rss><channel><item><title>x", NOW, "coindesk")

    def test_empty_body_raises(self):
        with self.assertRaises(IntakeError):
            rss_documents(b"", NOW, "coindesk")

    def test_oversized_body_rejected(self):
        with self.assertRaises(IntakeError):
            rss_documents(b"<rss>" + b"x" * 900_000, NOW, "coindesk")

    def test_non_dict_status_payload_rejected(self):
        with self.assertRaises(IntakeError):
            statuspage_incidents(b"[]", NOW, "coinbase_status")

    def test_unknown_status_source_rejected(self):
        with self.assertRaises(IntakeError):
            statuspage_incidents(b'{"incidents": []}', NOW, "not_a_source")


class DedupeTests(unittest.TestCase):
    def test_duplicate_items_produce_one_document(self):
        one = rss(dt(NOW - 120_000), slug="same")
        two = one.replace("https://example.test/same",
                          "https://example.test/same")  # identical item twice
        body = one.replace("</channel>", one.split("<item>")[1].join(
            ["<item>", "</item></channel>"]) if False else "")
        # simpler: build a feed with the same item repeated
        item = one[one.index("<item>"):one.index("</item>") + 7]
        feed = (one[:one.index("<item>")] + item + item + "</channel></rss>")
        docs = rss_documents(feed.encode(), NOW, "coindesk")
        self.assertEqual(len({d["event_id"] for d in docs}), 1,
                         "duplicate titles must yield one stable event_id")

    def test_different_titles_stay_separate(self):
        item1 = rss(dt(NOW - 120_000), slug="a")
        i1 = item1[item1.index("<item>"):item1.index("</item>") + 7]
        item2 = rss(dt(NOW - 120_000), title="Ethereum upgrade ships next week",
                    summary="Ethereum network upgrade", slug="b")
        i2 = item2[item2.index("<item>"):item2.index("</item>") + 7]
        feed = (item1[:item1.index("<item>")] + i1 + i2 + "</channel></rss>")
        docs = rss_documents(feed.encode(), NOW, "coindesk")
        self.assertEqual(len(docs), 2)


if __name__ == "__main__":
    unittest.main()

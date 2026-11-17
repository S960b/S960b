"""NEWS v2.1 — Bluesky live-envelope regressions (offline, no network).

Two defects found in production that made the fast lane impossible to satisfy:
  1. only the flat {"kind":"commit"} envelope was parsed, so every live frame
     was silently dropped;
  2. published_ms was read from obj["time_us"], but the live envelope nests the
     timestamp inside "payload" as an ISO string, so published_ms was always
     None and eligible_fast() could never be True.
Both are locked down here.
"""
import json
import time
import unittest

from v21_normalize import bluesky_jetstream, eligible_fast

NOW = 1_800_000_000_000
DID = "did:plc:iqoyar2mdkpg6mskhmri3nr6"
ALLOWED = {DID}


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ms / 1000))


def live(created_ms, text="Bitcoin ETF outflows hit a record level",
         rkey="3abc", collection="app.bsky.feed.post", op="create",
         did=DID, include_time=True):
    payload = {"$type": "network.bsky.jetstream.subscribeEvents#commit",
               "did": did, "operation": op, "collection": collection,
               "rkey": rkey,
               "record": {"$type": collection, "createdAt": iso(created_ms),
                          "text": text}}
    if include_time:
        payload["time"] = iso(created_ms)
    return json.dumps({"$type": "message", "payload": payload}).encode()


def flat(created_ms, did=DID, rkey="3abc"):
    return json.dumps({"kind": "commit", "did": did,
                       "commit": {"operation": "create",
                                  "collection": "app.bsky.feed.post",
                                  "rkey": rkey,
                                  "record": {"createdAt": iso(created_ms),
                                             "text": "Bitcoin ETF outflows"}}}).encode()


class LiveEnvelopeTests(unittest.TestCase):
    def test_live_nested_envelope_is_accepted(self):
        ev = bluesky_jetstream(live(NOW - 45_000), NOW, ALLOWED)
        self.assertIsNotNone(ev, "live nested envelope must parse")
        self.assertEqual(ev["source_kind"], "verified_social")

    def test_flat_documented_envelope_still_works(self):
        self.assertIsNotNone(bluesky_jetstream(flat(NOW - 45_000), NOW, ALLOWED))

    def test_published_ms_comes_from_payload_time_not_author_field(self):
        ev = bluesky_jetstream(live(NOW - 45_000), NOW, ALLOWED)
        self.assertIsNotNone(ev["published_ms"],
                             "published_ms must not be None for live frames")
        self.assertEqual(ev["clock_quality"], "network_received")

    def test_hour_old_live_post_is_not_fast(self):
        ev = bluesky_jetstream(live(NOW - 3_600_000), NOW, ALLOWED)
        self.assertFalse(eligible_fast(ev, NOW))

    def test_forty_five_second_old_post_is_fast(self):
        ev = bluesky_jetstream(live(NOW - 45_000), NOW, ALLOWED)
        self.assertTrue(eligible_fast(ev, NOW),
                        "a genuinely fresh verified post must be fast-eligible")

    def test_missing_payload_time_is_not_trusted_as_publisher_clock(self):
        ev = bluesky_jetstream(live(NOW - 45_000, include_time=False), NOW, ALLOWED)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["clock_quality"], "unverified_author_createdAt")
        self.assertFalse(eligible_fast(ev, NOW))

    def test_repost_is_never_a_news_event(self):
        self.assertIsNone(bluesky_jetstream(
            live(NOW - 45_000, collection="app.bsky.feed.repost"), NOW, ALLOWED))

    def test_delete_operation_is_not_news(self):
        self.assertIsNone(bluesky_jetstream(live(NOW - 45_000, op="delete"),
                                            NOW, ALLOWED))

    def test_non_allowlisted_did_is_dropped(self):
        self.assertIsNone(bluesky_jetstream(
            live(NOW - 45_000, did="did:plc:someoneelse"), NOW, ALLOWED))

    def test_future_network_time_is_rejected(self):
        with self.assertRaises(Exception):
            bluesky_jetstream(live(NOW + 30 * 60_000), NOW, ALLOWED)


if __name__ == "__main__":
    unittest.main()

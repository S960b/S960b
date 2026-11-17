"""NEWS v2.4 stage 9 — heartbeat producer contract and schedule semantics."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / 'source').is_dir() or \
    (HERE.parent / 'v24_probe.py').is_file() else HERE
for _cand in (ROOT / 'source', ROOT, HERE):
    if (_cand / 'v24_probe.py').is_file():
        sys.path.insert(0, str(_cand))
        break

import v24_probe as PR
import v24_sources as S

NOW = 1_800_000_000_000
BUCKET = 3_600_000
START = NOW - BUCKET


def spec(source_id, category, cadence=120_000, start_ms=START):
    return S.SourceSpec(source_id, category, "primary", 900,
                        start_ms=start_ms, cadence_ms=cadence)


def registry_for(*specs):
    base = [spec(f"{c}-one", c) for c in S.CATEGORIES]
    return S.SourceRegistry(list(specs) + base)


def state(beats, *, src="crypto-one", cadence=120_000, start_ms=START,
          grace=90_000):
    return S.heartbeat_state(beats, spec=spec(src, "crypto", cadence, start_ms),
                             bucket_start=start_ms, bucket_end=start_ms + BUCKET,
                             grace_ms=grace)


class ScheduleSemanticsTests(unittest.TestCase):
    """P0: one probe in thirty must not read as healthy."""

    def _full(self, ok=True):
        return [S.Heartbeat("crypto-one", START + i * 120_000, ok)
                for i in range(30)]

    def test_full_schedule_is_healthy(self):
        self.assertEqual(state(self._full()), S.HEALTHY)

    def test_one_probe_out_of_thirty_is_not_healthy(self):
        self.assertEqual(state([S.Heartbeat("crypto-one", START + 60_000, True)]),
                         S.STALE)

    def test_no_probes_is_missing(self):
        self.assertEqual(state([]), S.MISSING)

    def test_all_failed_is_failed(self):
        self.assertEqual(state(self._full(ok=False)), S.FAILED)

    def test_mid_gaps_while_last_answers_is_intermittent(self):
        beats = [b for i, b in enumerate(self._full()) if i not in (5, 6, 7)]
        self.assertEqual(state(beats), S.INTERMITTENT)

    def test_feed_dying_midway_is_stale(self):
        self.assertEqual(state(self._full()[:10]), S.STALE)

    def test_no_declared_schedule_is_unknown(self):
        no_sched = S.SourceSpec("crypto-one", "crypto", "primary", 900)
        self.assertEqual(
            S.heartbeat_state(self._full(), spec=no_sched,
                              bucket_start=START, bucket_end=START + BUCKET,
                              grace_ms=90_000), S.UNKNOWN)

    def test_probes_outside_the_bucket_do_not_count(self):
        beats = [S.Heartbeat("crypto-one", START - 30_000, True),
                 S.Heartbeat("crypto-one", NOW + 30_000, True)]
        self.assertEqual(state(beats), S.MISSING)

    def test_duplicate_probes_are_not_extra_credit(self):
        beats = self._full() + self._full()
        self.assertEqual(state(beats), S.HEALTHY)

    def test_grace_covers_a_slightly_late_probe(self):
        # Every probe is 60 s late. With grace 90 s the schedule is satisfied;
        # with grace 10 s nothing matches an expected instant, so the source
        # answered nothing that was owed - that is `failed`, not `stale`:
        # `stale` means the last expected instant went unanswered while others
        # did, which requires at least one match.
        beats = [S.Heartbeat("crypto-one", START + i * 120_000 + 60_000, True)
                 for i in range(30)]
        self.assertEqual(state(beats, grace=90_000), S.HEALTHY)
        self.assertEqual(state(beats, grace=10_000), S.FAILED)

    def test_spec_with_half_a_schedule_is_refused(self):
        with self.assertRaises(S.AdapterError):
            S.SourceRegistry([S.SourceSpec("x", "crypto", "primary", 900,
                                           cadence_ms=120)])


class CoverageIntegrationTests(unittest.TestCase):
    def test_downtime_crossing_bucket_reports_missing_then_healthy(self):
        reg = registry_for()
        full = [S.Heartbeat("crypto-one", START + i * 120_000, True)
                for i in range(30)]
        out = S.coverage_timeline([], start_ms=START, end_ms=START + 2 * BUCKET,
                                  bucket_ms=BUCKET, registry=reg,
                                  heartbeats=full)
        first = out["timeline"][0]["categories"]["crypto"]["availability"]
        second = out["timeline"][1]["categories"]["crypto"]["availability"]
        self.assertEqual(first["status"], S.HEALTHY)
        self.assertEqual(second["status"], S.MISSING)

    def test_uptime_stays_bounded_with_forged_source(self):
        reg = registry_for()
        ghost = S.Heartbeat("ghost", NOW - 60_000, True)
        out = S.coverage_timeline([], start_ms=START, end_ms=NOW + 1,
                                  bucket_ms=BUCKET, registry=reg,
                                  heartbeats=[ghost])
        cell = out["timeline"][0]["categories"]["crypto"]["availability"]
        self.assertEqual(cell["uptime"], 0.0)
        self.assertEqual(out["quarantined_heartbeats"][0]["reason"],
                         "heartbeat_unknown_source:ghost")

    def test_future_probe_is_quarantined_not_counted(self):
        reg = registry_for()
        out = S.coverage_timeline([], start_ms=START, end_ms=NOW + 1,
                                  bucket_ms=BUCKET, registry=reg,
                                  heartbeats=[S.Heartbeat("crypto-one",
                                                          NOW + 60_000, True)],
                                  cutoff_ms=NOW)
        self.assertEqual(out["quarantined_heartbeats"][0]["reason"],
                         "heartbeat_after_cutoff")

    def test_late_probe_requires_a_versioned_correction(self):
        reg = registry_for()
        out = S.coverage_timeline([], start_ms=START, end_ms=NOW + 1,
                                  bucket_ms=BUCKET, registry=reg, heartbeats=[])
        row = out["timeline"][0]
        late = S.Heartbeat("crypto-one", NOW - 60_000, True)
        corrected = S.apply_correction(
            {**row, "categories": {"crypto-one": {
                "availability": {"status": S.MISSING, "uptime": 0.0}}}},
            source_id="crypto-one", late_probe=late, reason="probe_log_recovered")
        self.assertEqual(corrected["version"], 2)
        self.assertEqual(corrected["correction"]["original_status"], S.MISSING)
        self.assertEqual(row.get("version"), None, "the original must not mutate")

    def test_correction_requires_a_reason(self):
        with self.assertRaises(S.AdapterError):
            S.apply_correction({"categories": {"crypto-one": {
                "availability": {"status": S.MISSING, "uptime": 0.0}}}},
                source_id="crypto-one",
                late_probe=S.Heartbeat("crypto-one", NOW, True), reason="  ")


class ProducerContractTests(unittest.TestCase):
    def test_attempt_ids_are_monotonic_per_source(self):
        store = PR.AttemptStore()
        a = store.record(source_id="bin", started_ms=1_000, finished_ms=1_100,
                         outcome="ok")
        b = store.record(source_id="bin", started_ms=2_000, finished_ms=2_100,
                         outcome="ok")
        c = store.record(source_id="eia", started_ms=3_000, finished_ms=3_100,
                         outcome="ok")
        self.assertEqual((a.attempt_id, b.attempt_id, c.attempt_id), (1, 2, 1))

    def test_unknown_outcome_is_refused(self):
        store = PR.AttemptStore()
        with self.assertRaises(S.AdapterError):
            store.record(source_id="bin", started_ms=1, finished_ms=2,
                         outcome="vibes")

    def test_negative_duration_refused(self):
        store = PR.AttemptStore()
        with self.assertRaises(S.AdapterError):
            store.record(source_id="bin", started_ms=10, finished_ms=5,
                         outcome="ok")

    def test_only_ok_counts_as_a_successful_probe(self):
        store = PR.AttemptStore()
        store.record(source_id="bin", started_ms=1, finished_ms=2,
                     outcome="rate_limited")
        store.record(source_id="bin", started_ms=3, finished_ms=4, outcome="ok")
        self.assertEqual([h.ok for h in store.heartbeats()], [False, True])

    def test_tampered_digest_is_refused(self):
        store = PR.AttemptStore()
        good = store.record(source_id="bin", started_ms=1, finished_ms=2,
                            outcome="ok")
        bad = PR.ProbeAttempt(**{**good.__dict__, "probe_sha256": "0" * 64})
        with self.assertRaises(S.AdapterError) as ctx:
            PR.to_heartbeat(bad)
        self.assertIn("digest_mismatch", str(ctx.exception))

    def test_retry_budget_is_bounded(self):
        store = PR.AttemptStore()
        for i in range(PR.MAX_PROBE_ATTEMPTS):
            store.record(source_id="bin", started_ms=i * 10, finished_ms=i * 10 + 1,
                         outcome="timeout")
        self.assertEqual(store.retry_budget("bin"), 0)

    def test_retention_is_reported_not_hidden(self):
        store = PR.AttemptStore()
        store.record(source_id="bin", started_ms=1, finished_ms=2, outcome="ok")
        kept = store.rows(now_ms=2 + PR.RETENTION_DAYS * 86_400_000 + 1)
        self.assertEqual(kept, [])
        self.assertEqual(len(store.rows()), 1, "pruning is a read filter")

    def test_probe_module_has_no_network_code(self):
        candidates = [ROOT / "v24_probe.py", HERE / "v24_probe.py",
                      ROOT / "source" / "v24_probe.py",
                      HERE / "source" / "v24_probe.py"]
        path = next((c for c in candidates if c.is_file()), None)
        self.assertIsNotNone(path)
        text = path.read_text(encoding="utf-8")
        for banned in ("import socket", "import urllib", "import requests",
                       "urlopen(", "requests.get", "socket.socket"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()

"""Static fail-closed checks for NEWS user-level collector/label schedules.

These tests never install a systemd unit or access production state.
"""
import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "deployment" / "systemd-user"
NAMES = (
    "news-phase4-collector.service", "news-phase4-collector.timer",
    "news-phase4-tier2-label.service", "news-phase4-tier2-label.timer",
)
RELEASE = "/home/kali/research/news_phase4_v25_release/research/news_phase4_v21"
LEGACY = "/home/kali/research/news_phase4_v24_live"


def unit(name):
    config = configparser.ConfigParser(interpolation=None)
    p = UNITS / name
    with p.open(encoding="utf-8") as inp:
        config.read_file(inp)
    return config, p.read_text(encoding="utf-8")


class UnitTests(unittest.TestCase):
    def test_four_distinct_units_exist(self):
        self.assertEqual({p.name for p in UNITS.glob("*.*")}, set(NAMES))

    def test_collector_runs_archive_only_and_bound(self):
        conf, raw = unit("news-phase4-collector.service")
        command = conf["Service"]["ExecStart"]
        self.assertIn(RELEASE + "/source/v22_live/collector_only.py", command)
        self.assertIn("--max-hours 12", command)
        self.assertIn("--interval-s 180", command)
        self.assertEqual(conf["Service"]["Restart"], "no")
        self.assertEqual(conf["Service"]["SuccessExitStatus"], "4 5 6")
        self.assertEqual(conf["Service"]["Type"], "simple")
        self.assertNotIn(LEGACY, raw)
        self.assertNotIn("--execute", command)

    def test_label_unit_cannot_invoke_any_model(self):
        conf, raw = unit("news-phase4-tier2-label.service")
        self.assertIn("ConditionPathExists", conf["Unit"])
        self.assertIn("news-phase4-v25/tier2.sqlite",
                      conf["Unit"]["ConditionPathExists"])
        command = conf["Service"]["ExecStart"]
        self.assertIn(RELEASE + "/source/v22_live/tier2_label_watch.py", command)
        self.assertIn("--settle", command)
        self.assertNotIn("--execute", command)
        self.assertNotIn(LEGACY, raw)
        self.assertEqual(conf["Service"]["Type"], "oneshot")

    def test_timer_targets_unique_service_and_5m_cadence(self):
        for base in ("news-phase4-collector", "news-phase4-tier2-label"):
            with self.subTest(base=base):
                conf, _ = unit(base + ".timer")
                self.assertEqual(conf["Timer"]["Unit"], base + ".service")
                self.assertEqual(conf["Timer"]["OnCalendar"], "*:0/5")
                self.assertEqual(conf["Timer"]["Persistent"], "true")
                self.assertEqual(conf["Install"]["WantedBy"], "timers.target")

    def test_services_pin_release_and_have_private_umask(self):
        for base in ("news-phase4-collector", "news-phase4-tier2-label"):
            with self.subTest(base=base):
                conf, raw = unit(base + ".service")
                self.assertEqual(conf["Service"]["WorkingDirectory"], RELEASE)
                self.assertIn(RELEASE + "/source",
                              conf["Service"]["Environment"])
                self.assertIn(RELEASE + "/source/v22_live",
                              conf["Service"]["Environment"])
                self.assertEqual(conf["Service"]["UMask"], "0077")
                self.assertNotIn("TRIGGER_ENABLED", conf["Service"]["ExecStart"])
                self.assertNotIn("TIER2_ACTIVE", conf["Service"]["ExecStart"])


if __name__ == "__main__":
    unittest.main()

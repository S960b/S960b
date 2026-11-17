"""F10: atomic collector claim across processes, safe old-PID coexistence.

No feed/network or live process; all state resides under TemporaryDirectory.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CODE = HERE / "source" / "v22_live"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))
import collector_only as C


class AtomicSingletonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pidfile = self.root / "collector.pid"

    def test_F10_stale_pid_then_atomic_claim_and_release(self):
        self.pidfile.write_text("999999")
        self.assertEqual(C.claim_singleton(self.pidfile), os.getpid())
        self.assertTrue((self.root / "collector.pid.lock").exists())
        self.assertEqual(int(self.pidfile.read_text()), os.getpid())
        self.assertTrue(C.release(self.pidfile))
        self.assertFalse(self.pidfile.exists())
        self.assertTrue((self.root / "collector.pid.lock").exists())

    def test_F10_foreign_live_legacy_pid_is_never_stolen(self):
        other = subprocess.Popen(["sleep", "5"])
        try:
            self.pidfile.write_text(str(other.pid))
            with self.assertRaisesRegex(C.SingletonRefused, "already_running"):
                C.claim_singleton(self.pidfile)
            self.assertEqual(self.pidfile.read_text(), str(other.pid))
        finally:
            other.terminate()
            other.wait(timeout=5)

    def test_F10_concurrent_claims_one_winner(self):
        # Eight processes share an identical start barrier and stale PID file.
        # Before the fix, each could observe no holder and overwrite the PID.
        self.pidfile.write_text("999999")
        start = self.root / "go"
        code = (
            "import sys,time,os\n"
            f"sys.path.insert(0,{str(CODE)!r})\n"
            "import collector_only as C\n"
            f"p={str(self.pidfile)!r}; g={str(start)!r}\n"
            "while not os.path.exists(g): time.sleep(0.005)\n"
            "try:\n"
            " C.claim_singleton(p)\n"
            " print('WIN',flush=True)\n"
            " time.sleep(1.2)\n"
            " C.release(p)\n"
            "except C.SingletonRefused:\n"
            " print('REFUSED',flush=True)\n"
        )
        workers = [
            subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
            for _ in range(8)
        ]
        try:
            start.write_text("go")
            results = [p.communicate(timeout=15) for p in workers]
            outcomes = [a.strip() for a, b in results]
            self.assertEqual(outcomes.count("WIN"), 1, results)
            self.assertEqual(outcomes.count("REFUSED"), 7, results)
            self.assertTrue(all(p.returncode == 0 for p in workers), results)
            self.assertTrue((self.root / "collector.pid.lock").exists())
        finally:
            for p in workers:
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=5)

    def test_F10_bad_external_pid_file_not_erased(self):
        self.assertEqual(C.claim_singleton(self.pidfile), os.getpid())
        self.pidfile.write_text("999998")
        self.assertFalse(C.release(self.pidfile))
        self.assertEqual(self.pidfile.read_text(), "999998")

    def test_F10_after_release_another_process_can_claim(self):
        self.assertEqual(C.claim_singleton(self.pidfile), os.getpid())
        self.assertTrue(C.release(self.pidfile))
        code = (
            "import sys\n"
            f"sys.path.insert(0,{str(CODE)!r})\n"
            "import collector_only as C\n"
            f"p={str(self.pidfile)!r}\n"
            "C.claim_singleton(p)\n"
            "print('WIN')\n"
            "C.release(p)\n"
        )
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("WIN", p.stdout)


if __name__ == "__main__":
    unittest.main()

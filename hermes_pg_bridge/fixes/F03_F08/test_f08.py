#!/usr/bin/env python3
"""F08 isolated tests: damaged session and independent gate/permit revocation."""
import importlib.util, json, os, pathlib, sys, tempfile, unittest
from unittest import mock

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,str(path))
    x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x);return x

class F08(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m37=load(pathlib.Path(sys.argv[1]),"wd37")
        cls.m39=load(pathlib.Path(sys.argv[2]),"keeper39")
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=pathlib.Path(self.tmp.name)
        self.home=p/"home";self.cap=self.home/".config"/"bridge-pg-v2"
        self.cap.mkdir(parents=True,mode=0o700);(self.home/".config").chmod(0o700)
        m=self.m39
        self.root=p/"stage39";self.root.mkdir(mode=0o700)
        for name,value in [("CAP",self.cap),("ROOT",self.root),("SESSION",self.root/"session.json"),
                           ("GATE",self.cap/"managed-writes-stage34d-armed"),
                           ("PERMIT",self.cap/"managed-mcp-write-permit35c.json"),
                           ("LOCK",self.cap/".stage37a-revoke.lock")]:
            q=mock.patch.object(m,name,value);q.start();self.addCleanup(q.stop)
        q=mock.patch.object(m,"owner",lambda:None);q.start();self.addCleanup(q.stop)
        m.GATE.write_bytes(m.GATE_BYTES)
        m.PERMIT.write_text('{"version":1}')
        m.SESSION.write_bytes(b'garbled JSON{')
    def test_corrupted_session_does_not_block_stop(self):
        m=self.m39
        with self.assertRaises(m.Stop):
            m.stop()
        self.assertFalse(m.GATE.exists())
        self.assertFalse(m.PERMIT.exists())
        self.assertTrue(m.SESSION.exists())
    def test_keeper_first_path_eio_still_revokes_second(self):
        m=self.m39
        actual=m.quarantine_one
        def fail_first(path):
            if path==m.GATE:raise OSError("injected EIO")
            return actual(path)
        with mock.patch.object(m,"quarantine_one",side_effect=fail_first):
            with self.assertRaises(m.Stop):m.stop()
        self.assertTrue(m.GATE.exists())
        self.assertFalse(m.PERMIT.exists())
    def test_keeper_second_path_eio_does_not_fake_success(self):
        m=self.m39
        actual=m.quarantine_one
        def fail_second(path):
            if path==m.PERMIT:raise OSError("injected EIO")
            return actual(path)
        with mock.patch.object(m,"quarantine_one",side_effect=fail_second):
            with self.assertRaises(m.Stop):m.stop()
        self.assertFalse(m.GATE.exists())
        self.assertTrue(m.PERMIT.exists())
    def test_watchdog_independent_emergency_revoke(self):
        m=self.m37
        gate=self.cap/m.GATE_NAME
        permit=self.cap/m.PERMIT_NAME
        actual=m.quarantine
        def fail_first(fd,name):
            if name==m.GATE_NAME:raise OSError("injected EIO")
            return actual(fd,name)
        with mock.patch.object(m,"quarantine",side_effect=fail_first):
            with self.assertRaises(m.Stop):m.revoke(self.home)
        self.assertTrue(gate.exists())
        self.assertFalse(permit.exists())
    def test_watchdog_normal_revoke(self):
        m=self.m37
        r=m.revoke(self.home)
        self.assertTrue(r["gate_quarantined"])
        self.assertTrue(r["permit_quarantined"])
        self.assertFalse(self.cap.joinpath(m.GATE_NAME).exists())
        self.assertFalse(self.cap.joinpath(m.PERMIT_NAME).exists())

if __name__=="__main__":
    unittest.main(argv=[sys.argv[0]],verbosity=1)

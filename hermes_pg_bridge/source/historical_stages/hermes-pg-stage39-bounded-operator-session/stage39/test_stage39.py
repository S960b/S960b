#!/usr/bin/env python3
"""Offline fault injection for Stage39; no real home, services, DB or plugins touched."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import tempfile
import time
import unittest
from unittest import mock

S=pathlib.Path(__file__).resolve().parent/'stage39.py'
spec=importlib.util.spec_from_file_location('stage39_under_test',S)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class TestPure(unittest.TestCase):
    def sample(self):
        now=2000000000
        return {'version':1,'session_id':'a'*32,'task_id':m.TASK,'config_sha256':m.CFG_SHA,'boot_id':'a'*8+'-'+ 'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
                'created_unix':now,'deadline_unix':now+21600,'status':'active','reason':'','last_renew_unix':now}
    def test_session_roundtrip(self): self.assertEqual(m.parse_session(json.dumps(self.sample()).encode())['status'],'active')
    def test_session_extra_key_rejected(self):
        d=self.sample();d['extra']=1
        with self.assertRaises(m.Stop):m.parse_session(json.dumps(d).encode())
    def test_session_wrong_task_rejected(self):
        d=self.sample();d['task_id']='task_'+'0'*32
        with self.assertRaises(m.Stop):m.parse_session(json.dumps(d).encode())
    def test_session_bad_id_rejected(self):
        d=self.sample();d['session_id']='bad'
        with self.assertRaises(m.Stop):m.parse_session(json.dumps(d).encode())
    def test_session_bad_deadline_rejected(self):
        d=self.sample();d['deadline_unix']+=1
        with self.assertRaises(m.Stop):m.parse_session(json.dumps(d).encode())
    def test_session_type_bool_timestamp_rejected(self):
        d=self.sample();d['last_renew_unix']=True
        with self.assertRaises(m.Stop):m.parse_session(json.dumps(d).encode())
    def test_make_permit_limited(self):self.assertEqual(m.make_permit(1000,3000)['expires_unix'],3000)
    def test_make_permit_one_hour(self):self.assertEqual(m.make_permit(1000,99999)['expires_unix'],4600)
    def test_make_permit_expired(self):
        with self.assertRaises(m.Stop):m.make_permit(1000,1000)
    def test_permit_no_create(self):self.assertEqual(m.make_permit(1000,4000)['task_ids'],[m.TASK]);self.assertIs(m.make_permit(1000,4000)['allow_create'],False)
    def test_exact_gate(self):self.assertEqual(m.GATE_BYTES,b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n')

class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        b=pathlib.Path(self.temp.name)
        self.cap=b/'cap';self.root=b/'root'
        self.cap.mkdir(mode=0o700);self.root.mkdir(mode=0o700)
        for name,value in [('ROOT',self.root),('SESSION',self.root/'session.json'),('CAP',self.cap),('PERMIT',self.cap/'managed-mcp-write-permit35c.json'),('GATE',self.cap/'managed-writes-stage34d-armed'),('LOCK',self.cap/'.stage37a-revoke.lock')]:
            p=mock.patch.object(m,name,value);p.start();self.addCleanup(p.stop)
        for name,replace in [('preflight_start',lambda:None),('owner',lambda:None),('trusted_dir',lambda path:None),('check_config',lambda:None),('watchdog_running',lambda:None),('boot_id',lambda:'a'*8+'-'+'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12)]:
            p=mock.patch.object(m,name,replace);p.start();self.addCleanup(p.stop)
        self.now=int(time.time())
    def run_quiet(self,fn,*a,**kw):
        with contextlib.redirect_stdout(io.StringIO()):return fn(*a,**kw)
    def open(self):self.run_quiet(m.start,self.now)
    def test_start_active_one_task(self):
        self.open()
        doc=m.session_read();self.assertEqual(doc['status'],'active')
        self.assertEqual(m.validate_permit(self.now,doc['deadline_unix'])['task_ids'],[m.TASK])
        m.validate_gate(self.now)
    def test_start_no_reuse_active(self):
        self.open()
        with self.assertRaises(m.Stop):self.run_quiet(m.start,self.now+1)
    def test_one_hour_expiry(self):
        self.open();self.assertEqual(m.validate_permit(self.now,self.now+21600)['expires_unix'],self.now+3600)
    def test_gate_refresh(self):
        self.open();before=m.GATE.stat().st_mtime
        self.run_quiet(m.tick,self.now+400)
        self.assertGreater(m.GATE.stat().st_mtime,before+350)
        self.assertEqual(m.session_read()['status'],'active')
    def test_permit_renewed_before_hour(self):
        self.open()
        for sec in range(300,2401,300):self.run_quiet(m.tick,self.now+sec)
        self.assertGreater(m.validate_permit(self.now+2400,self.now+21600)['expires_unix'],self.now+3600)
        self.assertEqual(m.session_read()['status'],'active')
    def test_past_gate_deadline_fails_closed(self):
        self.open();self.run_quiet(m.tick,self.now+1201)
        self.assertEqual(m.session_read()['status'],'fault');self.assertFalse(m.PERMIT.exists());self.assertFalse(m.GATE.exists())
    def test_missing_gate_does_not_rearm(self):
        self.open();m.GATE.unlink();self.run_quiet(m.tick,self.now+5)
        self.assertFalse(m.GATE.exists());self.assertFalse(m.PERMIT.exists());self.assertEqual(m.session_read()['status'],'fault')
    def test_missing_permit_does_not_rearm(self):
        self.open();m.PERMIT.unlink();self.run_quiet(m.tick,self.now+5)
        self.assertFalse(m.PERMIT.exists());self.assertFalse(m.GATE.exists());self.assertEqual(m.session_read()['status'],'fault')
    def test_untrusted_gate_fails_closed(self):
        self.open();m.GATE.write_bytes(b'wrong\n');self.run_quiet(m.tick,self.now+4)
        self.assertEqual(m.session_read()['status'],'fault');self.assertFalse(m.PERMIT.exists())
    def test_wrong_permit_task_fails_closed(self):
        self.open();d=json.loads(m.PERMIT.read_text());d['task_ids']=['task_'+'a'*32];m.PERMIT.write_text(json.dumps(d));self.run_quiet(m.tick,self.now+5)
        self.assertEqual(m.session_read()['status'],'fault');self.assertFalse(m.GATE.exists())
    def test_boot_changed_fails_closed(self):
        self.open()
        with mock.patch.object(m,'boot_id',return_value='b'*36):self.run_quiet(m.tick,self.now+3)
        self.assertEqual(m.session_read()['reason'],'BOOT_CHANGED');self.assertFalse(m.GATE.exists())
    def test_hard_limit_fails_closed(self):
        self.open();self.run_quiet(m.tick,self.now+21600)
        self.assertEqual(m.session_read()['status'],'closed');self.assertFalse(m.GATE.exists());self.assertFalse(m.PERMIT.exists())
    def test_stop_idempotent(self):
        self.open();self.run_quiet(m.stop);self.run_quiet(m.stop)
        self.assertEqual(m.session_read()['status'],'closed');self.assertFalse(m.GATE.exists());self.assertFalse(m.PERMIT.exists())
    def test_start_again_after_stop(self):
        self.open();first=m.session_read()['session_id'];self.run_quiet(m.stop);self.run_quiet(m.start,self.now+10)
        self.assertNotEqual(m.session_read()['session_id'],first)
    def test_corrupted_session_revokes(self):
        self.open();m.SESSION.write_bytes(b'garbled');self.run_quiet(m.tick,self.now+10)
        self.assertFalse(m.GATE.exists());self.assertFalse(m.PERMIT.exists())
    def test_no_session_does_not_arm(self):
        self.run_quiet(m.tick,self.now);self.assertFalse(m.GATE.exists());self.assertFalse(m.PERMIT.exists())
    def test_no_session_stray_caps_quarantined(self):
        m.GATE.write_bytes(m.GATE_BYTES);self.run_quiet(m.tick,self.now)
        self.assertFalse(m.GATE.exists())
    def test_closed_session_stray_caps_quarantined(self):
        self.open();self.run_quiet(m.stop)
        m.GATE.write_bytes(m.GATE_BYTES);self.run_quiet(m.tick,self.now+50)
        self.assertFalse(m.GATE.exists())
    def test_unexpected_config_failure_fails_closed(self):
        self.open()
        with mock.patch.object(m,'check_config',side_effect=m.Stop('STAGE39_STOP_CONFIG_SHA_CHANGED')):
            self.run_quiet(m.tick,self.now+5)
        self.assertEqual(m.session_read()['status'],'fault');self.assertFalse(m.GATE.exists());self.assertFalse(m.PERMIT.exists())
    def test_quarantined_old_permit_preserved(self):
        self.open();old=m.PERMIT.read_bytes();self.run_quiet(m.stop)
        copies=list(self.cap.glob('.revoked-stage39-managed-mcp-write-permit*.bak'))
        self.assertEqual(len(copies),1);self.assertEqual(copies[0].read_bytes(),old)
    def test_status_has_no_writes(self):
        self.open();old=m.SESSION.read_bytes()
        self.run_quiet(m.status)
        self.assertEqual(m.SESSION.read_bytes(),old)
    def test_permit_mode_0600(self):
        self.open();self.assertEqual(stat.S_IMODE(m.PERMIT.stat().st_mode),0o600)
    def test_private_session_mode_0600(self):
        self.open();self.assertEqual(stat.S_IMODE(m.SESSION.stat().st_mode),0o600)
    def test_gate_touch_does_not_change_contents(self):
        self.open();old=m.GATE.read_bytes();self.run_quiet(m.tick,self.now+310)
        self.assertEqual(m.GATE.read_bytes(),old)
    def test_same_task_after_restart_with_caps(self):
        self.open();self.run_quiet(m.tick,self.now+20)
        self.assertEqual(m.session_read()['status'],'active')
    def test_expired_permit_no_recovery(self):
        self.open();d=json.loads(m.PERMIT.read_text());d['expires_unix']=self.now-10;m.PERMIT.write_bytes(m.permit_blob(d))
        self.run_quiet(m.tick,self.now+20)
        self.assertEqual(m.session_read()['status'],'fault');self.assertFalse(m.PERMIT.exists())

if __name__=='__main__':unittest.main(verbosity=1)

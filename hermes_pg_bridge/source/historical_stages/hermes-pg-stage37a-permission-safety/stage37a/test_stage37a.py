#!/usr/bin/env python3
"""Offline fixture tests; never touch /home/kali, PostgreSQL, or systemd."""
from __future__ import annotations
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

sp=importlib.util.spec_from_file_location('stage37a',Path(__file__).with_name('stage37a.py'))
m=importlib.util.module_from_spec(sp)
sp.loader.exec_module(m)

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='stage37a-tests-')
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        (self.home/'.config').mkdir(mode=0o700)
        self.d=self.home/m.ACTIVE_DIR
        self.d.mkdir(mode=0o700)
        self.cfg=self.home/m.CFG
        self.cfg.parent.mkdir(mode=0o700,parents=True)
        self.cfg.write_bytes(b'{"plugin": []}\n')
        self.permit=self.d/m.PERMIT_NAME
        self.gate=self.d/m.GATE_NAME
        self.t=1795464516

    def put(self,path,content,mode=0o600):
        path.write_bytes(content)
        path.chmod(mode)
        if path==self.gate:
            os.utime(path,(self.t,self.t))

    def issue(self,**changes):
        p={'version':1,'expires_unix':self.t+1800,
           'config_sha256':m.hashlib.sha256(self.cfg.read_bytes()).hexdigest(),
           'task_ids':['task_'+'a'*32],'allow_create':False,'static_tunnel_verified':True}
        p.update(changes)
        self.put(self.permit,(json.dumps(p)+'\n').encode())
        return p

    def test_status_absent(self):
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'ABSENT')
    def test_status_does_not_create_lock(self):
        m.status(self.home,self.t)
        self.assertFalse((self.d/m.LOCK_NAME).exists())
    def test_valid_scoped(self):
        self.issue()
        s=m.status(self.home,self.t)
        self.assertTrue(s['permit_valid'])
        self.assertEqual(s['permit_state'],'VALID_SCOPED')
    def test_create_scope_classification(self):
        self.issue(task_ids=[],allow_create=True)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'VALID_CREATE')
    def test_malformed_rejected(self):
        self.put(self.permit,b'{')
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'SIZE_INVALID')
    def test_expired_rejected(self):
        self.issue(expires_unix=self.t)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'EXPIRED_OR_BAD_EXPIRY')
    def test_future_expiry_rejected(self):
        self.issue(expires_unix=self.t+86401)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'EXPIRED_OR_BAD_EXPIRY')
    def test_config_drift_detected(self):
        self.issue()
        self.cfg.write_bytes(b'{"plugin":[1]}')
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'CONFIG_DRIFT')
    def test_symlink_not_followed_and_target_preserved(self):
        target=self.home/'innocent'
        target.write_bytes(b'keep')
        self.permit.symlink_to(target)
        self.gate.symlink_to(target)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'UNSAFE_FILE')
        m.revoke(self.home)
        self.assertEqual(target.read_bytes(),b'keep')
        self.assertFalse(self.permit.is_symlink())
        self.assertFalse(self.gate.is_symlink())
    def test_revoke_any_schema_or_config_drift(self):
        self.put(self.permit,b'unknown content')
        self.put(self.gate,b'unknown gate content')
        r=m.revoke(self.home)
        self.assertTrue(r['permit_quarantined'])
        self.assertTrue(r['gate_quarantined'])
        self.assertFalse(self.permit.exists())
        self.assertFalse(self.gate.exists())
        self.assertEqual(len(list(self.d.glob('.revoked-stage37a-*.bak'))),2)
    def test_revoke_idempotent(self):
        self.issue()
        m.revoke(self.home)
        r=m.revoke(self.home)
        self.assertEqual(r,{'gate_quarantined':False,'permit_quarantined':False})
    def test_revoke_no_plugins_changed(self):
        original=self.cfg.read_bytes()
        self.issue()
        m.revoke(self.home)
        self.assertEqual(self.cfg.read_bytes(),original)
    def test_non_private_file_revoked(self):
        self.issue()
        self.permit.chmod(0o644)
        self.assertFalse(m.status(self.home,self.t)['permit_valid'])
        self.assertTrue(m.revoke(self.home)['permit_quarantined'])
    def test_watchdog_invalid_revoke_both(self):
        self.issue()
        self.cfg.write_bytes(b'changed')
        self.put(self.gate,m.GATE_BYTES)
        r=m.watchdog(self.home,self.t)
        self.assertEqual(r['action'],'REVOKED_INVALID')
        self.assertEqual(r['reason'],'CONFIG_DRIFT')
        self.assertFalse(self.permit.exists())
        self.assertFalse(self.gate.exists())
    def test_watchdog_invalid_json(self):
        self.put(self.permit,b'not json')
        self.put(self.gate,m.GATE_BYTES)
        self.assertEqual(m.watchdog(self.home,self.t)['action'],'REVOKED_INVALID')
    def test_watchdog_expiry(self):
        self.issue(expires_unix=self.t)
        self.assertEqual(m.watchdog(self.home,self.t)['reason'],'EXPIRED_OR_BAD_EXPIRY')
        self.assertFalse(self.permit.exists())
    def test_watchdog_scope_with_gate_retained(self):
        self.issue()
        self.put(self.gate,m.GATE_BYTES)
        self.assertEqual(m.watchdog(self.home,self.t)['action'],'RETAINED_VALID')
        self.assertTrue(self.gate.exists())
    def test_watchdog_create_with_gate_revoked(self):
        self.issue(task_ids=[],allow_create=True)
        self.put(self.gate,m.GATE_BYTES)
        r=m.watchdog(self.home,self.t)
        self.assertEqual(r['action'],'REVOKED_UNSAFE_GATE')
        self.assertTrue(self.permit.exists())
        self.assertFalse(self.gate.exists())
    def test_watchdog_orphan_gate_revoked(self):
        self.put(self.gate,m.GATE_BYTES)
        self.assertEqual(m.watchdog(self.home,self.t)['action'],'REVOKED_INVALID')
        self.assertFalse(self.gate.exists())
    def test_watchdog_absence_idempotent(self):
        self.assertEqual(m.watchdog(self.home,self.t)['action'],'REVOKED_INVALID')
        self.assertEqual(m.watchdog(self.home,self.t)['action'],'REVOKED_INVALID')
    def test_watchdog_does_not_extend_expiry(self):
        p=self.issue()
        m.watchdog(self.home,self.t)
        self.assertEqual(json.loads(self.permit.read_text())['expires_unix'],p['expires_unix'])
    def test_wrong_scope_rejected(self):
        self.issue(task_ids=['task_'+ 'b'*32,'task_'+'c'*32])
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'INVALID_SCOPE')
    def test_bool_expiry_rejected(self):
        self.issue(expires_unix=True)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'EXPIRED_OR_BAD_EXPIRY')
    def test_unknown_field_rejected(self):
        self.issue(extra=1)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'INVALID_SCHEMA')
    def test_directory_at_active_file_fails_closed(self):
        self.permit.mkdir()
        with self.assertRaises(m.Stop):m.revoke(self.home)
    def test_symlink_directory_disallowed(self):
        d=self.d
        d.rename(d.with_name('bridge-backup'))
        d.symlink_to(d.with_name('bridge-backup'),target_is_directory=True)
        with self.assertRaises(m.Stop):m.status(self.home,self.t)
    def test_untrusted_world_writable_directory_disallowed(self):
        self.d.chmod(0o777)
        with self.assertRaises(m.Stop):m.revoke(self.home)
    def test_gate_revoked_before_permit_on_manual_revoke(self):
        self.issue()
        self.put(self.gate,m.GATE_BYTES)
        r=m.revoke(self.home)
        self.assertTrue(r['gate_quarantined'] and r['permit_quarantined'])
    def test_no_unrelated_backups_removed(self):
        self.put(self.d/'.revoked-permit35c.earlier.bak',b'earlier')
        m.revoke(self.home)
        self.assertEqual((self.d/'.revoked-permit35c.earlier.bak').read_bytes(),b'earlier')
    def test_canary_never_permitted(self):
        self.issue(task_ids=[m.CANARY])
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'INVALID_SCOPE')
    def test_bad_version_disallowed(self):
        self.issue(version=2)
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'INVALID_VERSION')
    def test_bad_sha_disallowed(self):
        self.issue(config_sha256='bad')
        self.assertEqual(m.status(self.home,self.t)['permit_state'],'INVALID_CONFIG_PIN')
    def test_too_old_gate_revoked_while_permit_valid(self):
        self.issue()
        self.put(self.gate,m.GATE_BYTES)
        os.utime(self.gate,(self.t-1201,self.t-1201))
        r=m.watchdog(self.home,self.t)
        self.assertEqual(r['reason'],'EXPIRED_GATE')
        self.assertFalse(self.gate.exists())
        self.assertTrue(self.permit.exists())
    def test_unknown_gate_revoked_while_permit_valid(self):
        self.issue()
        self.put(self.gate,b'rogue')
        self.assertEqual(m.watchdog(self.home,self.t)['reason'],'UNKNOWN_GATE')
        self.assertFalse(self.gate.exists())
    def test_gate_present_independent_of_json_structure(self):
        self.put(self.gate,b'x')
        self.put(self.permit,b'garbage')
        self.assertTrue(m.status(self.home,self.t)['cli_gate_present'])
        m.revoke(self.home)
        self.assertFalse(m.status(self.home,self.t)['cli_gate_present'])

if __name__=='__main__':unittest.main()

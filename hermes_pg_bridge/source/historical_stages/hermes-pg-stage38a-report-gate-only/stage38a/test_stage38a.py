import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('gate38a', ROOT / '510_arm_gate_only.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class Stage38ATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cfg = {'plugin': ['a', 'b', 'c', 'd', 'e', 'f', str(mod.CANARY),
                  [str(mod.CANDIDATE), {'dry_run': False, 'tick_ms': 30000,
                                        'task_ids': [mod.TASK]}]]}
        self.cb = (json.dumps(self.cfg, sort_keys=True)+'\n').encode()
        self.now = 1795526400
        self.permit = {'version': 1, 'expires_unix': self.now + 1800,
                       'config_sha256': mod.sha(self.cb), 'task_ids': [mod.TASK],
                       'allow_create': False, 'static_tunnel_verified': True}
        self.patches = mock.patch.object(mod, 'CFG_SHA', mod.sha(self.cb))
        self.patches.start(); self.addCleanup(self.patches.stop)

    def payload(self, d=None):
        return json.dumps(self.permit if d is None else d).encode()

    def test_exact_scoped_permit(self):
        self.assertEqual(mod.validate_permit(self.cb, self.payload(), self.now), self.now + 1800)
    def test_reject_expired(self):
        self.permit['expires_unix'] = self.now - 1
        with self.assertRaisesRegex(mod.Stop,'PERMIT_EXPIRED_OR_TOO_CLOSE'):
            mod.validate_permit(self.cb, self.payload(), self.now)
    def test_reject_too_close(self):
        self.permit['expires_unix'] = self.now + 179
        with self.assertRaisesRegex(mod.Stop,'PERMIT_EXPIRED_OR_TOO_CLOSE'):
            mod.validate_permit(self.cb, self.payload(), self.now)
    def test_reject_extended_permit(self):
        self.permit['expires_unix'] = self.now + 86401
        with self.assertRaisesRegex(mod.Stop,'PERMIT_EXPIRED_OR_TOO_CLOSE'):
            mod.validate_permit(self.cb, self.payload(), self.now)
    def test_reject_unscoped_create(self):
        self.permit['allow_create'] = True
        with self.assertRaisesRegex(mod.Stop,'PERMIT_NOT_SCOPED'):
            mod.validate_permit(self.cb, self.payload(), self.now)
    def test_reject_extra_task(self):
        self.permit['task_ids'].append('task_'+'a'*32)
        with self.assertRaisesRegex(mod.Stop,'PERMIT_NOT_SCOPED'):
            mod.validate_permit(self.cb, self.payload(), self.now)
    def test_reject_wrong_sha(self):
        self.permit['config_sha256'] = 'f'*64
        with self.assertRaisesRegex(mod.Stop,'PERMIT_NOT_SCOPED'):
            mod.validate_permit(self.cb, self.payload(), self.now)
    def test_reject_other_plugin(self):
        self.cfg['plugin'][5] = 'zz'
        with self.assertRaisesRegex(mod.Stop,'CONFIG_SHA_DRIFT'):
            mod.validate_permit(json.dumps(self.cfg).encode(),self.payload(), self.now)
    def test_reject_wrong_scope(self):
        self.cfg['plugin'][7][1]['task_ids'] = ['task_'+'a'*32]
        b = json.dumps(self.cfg).encode()
        with mock.patch.object(mod,'CFG_SHA',mod.sha(b)):
            with self.assertRaisesRegex(mod.Stop, 'MANAGED_PLUGIN_SCOPE_CHANGED'):
                mod.validate_permit(b,self.payload(),self.now)
    def test_reject_different_file_fields(self):
        self.permit['surprise'] = True
        with self.assertRaisesRegex(mod.Stop,'PERMIT_SCHEMA_CHANGED'):
            mod.validate_permit(self.cb,self.payload(),self.now)
    def test_reject_bool_as_expiry(self):
        self.permit['expires_unix'] = True
        with self.assertRaisesRegex(mod.Stop,'PERMIT_EXPIRED_OR_TOO_CLOSE'):
            mod.validate_permit(self.cb,self.payload(),self.now)
    def test_reject_missing_tunnel_pin(self):
        self.permit['static_tunnel_verified'] = False
        with self.assertRaisesRegex(mod.Stop,'PERMIT_NOT_SCOPED'):
            mod.validate_permit(self.cb,self.payload(),self.now)
    def test_create_gate_exact_contents_mode(self):
        gate = self.base/'managed-writes-stage34d-armed'
        mod.create_exclusive_gate(gate)
        self.assertEqual(gate.read_bytes(),mod.GATE_BYTES)
        self.assertEqual(stat.S_IMODE(gate.stat().st_mode),0o600)
    def test_gate_repeated_does_not_refresh_ttl(self):
        gate = self.base/'managed-writes-stage34d-armed'
        mod.create_exclusive_gate(gate)
        st = gate.stat().st_mtime_ns
        with self.assertRaisesRegex(mod.Stop,'GATE_ALREADY_PRESENT'):
            mod.create_exclusive_gate(gate)
        self.assertEqual(st,gate.stat().st_mtime_ns)
    def test_gate_symlink_not_followed(self):
        target = self.base/'victim';target.write_text('untouched')
        gate=self.base/'managed-writes-stage34d-armed';gate.symlink_to(target)
        with self.assertRaisesRegex(mod.Stop,'GATE_ALREADY_PRESENT'):
            mod.create_exclusive_gate(gate)
        self.assertEqual(target.read_text(),'untouched')
    def test_gate_partial_failure_quarantined(self):
        gate=self.base/'managed-writes-stage34d-armed'
        with mock.patch.object(mod,'sync_dir',side_effect=OSError('simulated fsync failure')):
            with self.assertRaises(OSError):mod.create_exclusive_gate(gate)
        self.assertFalse(gate.exists())
        self.assertEqual(len(list(self.base.glob('.stage38a-aborted-gate-*.bak'))),1)
    def test_real_script_minus_I_no_sibling_import(self):
        r=subprocess.run([sys.executable,'-IB',str(ROOT/'510_arm_gate_only.py')],
                         capture_output=True,text=True,check=False)
        # In CI environment, script fails closed on identity preflight but imports successfully.
        self.assertNotIn('ModuleNotFoundError',r.stderr)
        self.assertNotIn('Traceback',r.stderr)
        self.assertNotEqual(r.returncode,0)
    def test_read_checked_forbids_symlink(self):
        target=self.base/'target';target.write_bytes(b'xx')
        link=self.base/'link';link.symlink_to(target)
        with self.assertRaises(mod.Stop): mod.read_checked(link)
    def test_read_checked_private_mode(self):
        p=self.base/'permit';p.write_bytes(b'xx');p.chmod(0o644)
        with self.assertRaises(mod.Stop): mod.read_checked(p,private=True)
    def test_whitelist_rejects_none(self):
        self.cfg['plugin'][0] = None
        b=json.dumps(self.cfg).encode()
        with mock.patch.object(mod,'CFG_SHA',mod.sha(b)):
            with self.assertRaises(mod.Stop):mod.validate_permit(b,self.payload(),self.now)

if __name__=='__main__':unittest.main(verbosity=2)

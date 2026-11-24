"""Strictly offline tests; no live services, files or database are used."""
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import stage36b as x

CANARY = [str(x.CANARY_JS), {'dry_run':False,'task_ids':[x.CANARY],'tick_ms':30000}]
BASE = {'model':'demo','plugin':[
    '/home/kali/demo0.mjs','/home/kali/demo1.mjs',
    ['/home/kali/demo2.mjs',{'k':1}],'/home/kali/demo3.mjs',
    '/home/kali/demo4.mjs','/home/kali/demo5.mjs',CANARY,
]}

def raw(o=None): return json.dumps(BASE if o is None else o).encode('utf8')

def permit(**changes):
    d={'version':1,'expires_unix':int(time.time()+3600),'config_sha256':x.CFG7_SHA,
       'task_ids':[x.TASK],'allow_create':False,'static_tunnel_verified':True}
    d.update(changes)
    return x.json_bytes(d)

class Stage36BTests(unittest.TestCase):
    def test_new_config_eight(self):self.assertEqual(len(json.loads(x.build_cfg(raw()))['plugin']),8)
    def test_existing_plugins_preserved(self):self.assertEqual(json.loads(x.build_cfg(raw()))['plugin'][:7],BASE['plugin'])
    def test_only_plugin_key_changed(self):
        o=json.loads(x.build_cfg(raw()));o.pop('plugin');self.assertEqual(o,{'model':'demo'})
    def test_new_plugin_path(self):self.assertEqual(json.loads(x.build_cfg(raw()))['plugin'][-1][0],str(x.CANDIDATE))
    def test_new_plugin_only_task(self):self.assertEqual(json.loads(x.build_cfg(raw()))['plugin'][-1][1]['task_ids'],[x.TASK])
    def test_new_plugin_dry_run_false(self):self.assertFalse(json.loads(x.build_cfg(raw()))['plugin'][-1][1]['dry_run'])
    def test_new_plugin_tick_30(self):self.assertEqual(json.loads(x.build_cfg(raw()))['plugin'][-1][1]['tick_ms'],30000)
    def test_canary_unique(self):self.assertNotEqual(x.CANARY,x.TASK)
    def test_seven_count_fail(self):
        d=json.loads(raw());d['plugin'].pop()
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_duplicate_candidate_fail(self):
        d=json.loads(raw());d['plugin'][0]=str(x.CANDIDATE)
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_canary_removed_fail(self):
        d=json.loads(raw());d['plugin'][6]='/tmp/canary.mjs'
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_canary_dryrun_changed_fail(self):
        d=json.loads(raw());d['plugin'][6][1]['dry_run']=True
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_canary_task_scope_changed_fail(self):
        d=json.loads(raw());d['plugin'][6][1]['task_ids']=[x.TASK]
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_bad_plugin_shape_fail(self):
        d=json.loads(raw());d['plugin'][0]={'mjs':'/foo'}
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_git_plugin_fail(self):
        d=json.loads(raw());d['plugin'][0]='/tmp/mt-git-inbox.mjs'
        with self.assertRaises(x.Stop):x.build_cfg(raw(d))
    def test_permit_scoped(self):self.assertEqual(x.validate_permit(permit(),x.CFG7_SHA)['task_ids'],[x.TASK])
    def test_permit_create_must_be_false(self):
        with self.assertRaises(x.Stop):x.validate_permit(permit(allow_create=True),x.CFG7_SHA)
    def test_permit_task_cannot_be_old_pilot(self):
        with self.assertRaises(x.Stop):x.validate_permit(permit(task_ids=[x.OLD_PILOT]),x.CFG7_SHA)
    def test_permit_cannot_have_multiple_tasks(self):
        with self.assertRaises(x.Stop):x.validate_permit(permit(task_ids=[x.TASK,x.OLD_PILOT]),x.CFG7_SHA)
    def test_permit_expired_fails(self):
        with self.assertRaises(x.Stop):x.validate_permit(permit(expires_unix=int(time.time()-1)),x.CFG7_SHA)
    def test_permit_short_lived_fails(self):
        with self.assertRaises(x.Stop):x.validate_permit(permit(expires_unix=int(time.time()+100)),x.CFG7_SHA,min_secs=900)
    def test_permit_wrong_hash_fails(self):
        with self.assertRaises(x.Stop):x.validate_permit(permit(config_sha256='wrong'),x.CFG7_SHA)
    def test_permit_schema_exact(self):
        d=json.loads(permit());d['extra']=True
        with self.assertRaises(x.Stop):x.validate_permit(x.json_bytes(d),x.CFG7_SHA)
    def test_rebind_preserves_expiry_and_scope(self):
        old=permit();new=x.new_permit(old,'f'*64)
        a,b=map(json.loads,(old,new))
        self.assertEqual({k:v for k,v in a.items() if k!='config_sha256'},
                         {k:v for k,v in b.items() if k!='config_sha256'})
        self.assertEqual(b['config_sha256'],'f'*64)
    def test_legacy_method_send_not_in_activation(self):
        src=Path(x.__file__).read_text(); commit=src.split('def commit():',1)[1].split('\ndef revoke_gate():',1)[0]
        for term in ('cg_send_managed_task','cg_set_managed_dispatch','session.prompt','os.kill('):
            self.assertNotIn(term,commit)
    def test_gate_exact_bytes(self):self.assertEqual(x.GATE_BYTES,b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n')
    def test_gate_expiry_unit(self):
        s,t=x.service_content()
        self.assertIn(b'OnCalendar=',t)
        self.assertIn(b'Persistent=true',t)
        self.assertIn(b'revoke-gate',s)
        self.assertNotIn(b'ExecStartPre=',s)
    def test_readonly_snapshot_sql(self):
        import inspect
        s=inspect.getsource(x.db_snapshot)
        for token in ('INSERT ','UPDATE ','DELETE ','cg_set_managed_dispatch','cg_send_managed_task'):
            self.assertNotIn(token,s)
        self.assertEqual(s.count('SET TRANSACTION READ ONLY'),2)
    def test_no_sdk_call_in_package(self):
        src=Path(x.__file__).read_text()
        self.assertNotIn('session.prompt(',src.replace("'session.prompt('",''))
    def test_requires_multitool_quiet_before_changes(self):
        import inspect
        src=inspect.getsource(x.commit)
        self.assertLess(src.index('check_quiet()'),src.index('atom_replace(CFG'))
    def test_gate_created_last(self):
        import inspect
        src=inspect.getsource(x.commit)
        self.assertLess(src.index('atom_replace(PERMIT'),src.index('atom_create(GATE'))
    def test_allow_create_false_in_permit_rebind(self):
        x.validate_permit(x.new_permit(permit(),'f'*64),'f'*64)

if __name__=='__main__':unittest.main()

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import handoff33ib as h

OLD='/home/kali/hermes-pg-stage33f-sdk-preflight/mt-pg-inbox-agent-stage33f.mjs'
NEW='/home/kali/hermes-pg-stage33i-ackfix/mt-pg-inbox-agent-stage33i.mjs'
CANARY='task_f2c64cfdf7894537b35a19a6def977c6'

def config():
    return {'plugin':[
       '/tmp/mt-test-plugin.mjs',
       ['/tmp/linkedin-daily.mjs',{'enabled':True}],
       '/tmp/news-mt-plugin.mjs',
       '/tmp/news-p3-plugin.mjs',
       '/tmp/news-p4-v2-plugin.mjs',
       '/tmp/v21_plugin.mjs',
       [OLD,{'dry_run':False,'task_ids':[CANARY],'tick_ms':30000}]],
       'name':'unchanged', 'unrelated':{'a':[1,2,3]}}

def encode(x): return (json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode()

class Tests(unittest.TestCase):
    def test_core_replacement(self):
        x=config(); b=encode(x); y=json.loads(h.transformed_bytes(b))
        self.assertEqual(y['plugin'][-1][0],NEW)
        self.assertEqual(len(y['plugin']),7)
        self.assertEqual(y['plugin'][:-1],x['plugin'][:-1])
        self.assertEqual(y['plugin'][-1][1],x['plugin'][-1][1])
        self.assertEqual(y['unrelated'],x['unrelated'])
    def test_pure_does_not_mutate_original(self):
        b=encode(config());h.transformed_bytes(b);self.assertEqual(b,encode(config()))
    def test_reject_six(self):
        x=config();x['plugin'].pop(1)
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_eight(self):
        x=config();x['plugin'].append('extra')
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_duplicate(self):
        x=config();x['plugin'][0]=x['plugin'][-1]
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_new_already_present(self):
        x=config();x['plugin'][0]=NEW
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_old_git_plugin(self):
        x=config();x['plugin'][0]='/tmp/mt-git-inbox.mjs'
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_old_canary_plugin(self):
        x=config();x['plugin'][0]='/tmp/mt-pg-inbox-canary.mjs'
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_dryrun(self):
        x=config();x['plugin'][-1][1]['dry_run']=True
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_other_task(self):
        x=config();x['plugin'][-1][1]['task_ids']=['task_'+'a'*32]
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_reject_bad_entry(self):
        x=config();x['plugin'][-1]={'plugin':OLD}
        with self.assertRaises(h.Stop):h.transformed_bytes(encode(x))
    def test_hash(self):
        self.assertEqual(h.sha(b'abc'),hashlib.sha256(b'abc').hexdigest())
    def test_state_no_inflight(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'state.json'
            f.write_text(json.dumps({'sessions':{},'seen':{CANARY:5},'inflight':{}}))
            f.chmod(0o600)
            with patch.object(h,'STATE_DIR',Path(d)):
                self.assertEqual(h.durable_state_guard(),hashlib.sha256(f.read_bytes()).hexdigest())
    def test_state_reject_unresolved_inflight(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'state.json'
            f.write_text(json.dumps({'sessions':{},'seen':{CANARY:5},'inflight':{CANARY:{'seq':5}}}))
            f.chmod(0o600)
            with patch.object(h,'STATE_DIR',Path(d)):
                with self.assertRaises(h.Stop):h.durable_state_guard()
    def test_state_reject_wrong_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'state.json'
            f.write_text(json.dumps({'sessions':{},'seen':{CANARY:4},'inflight':{}}))
            f.chmod(0o600)
            with patch.object(h,'STATE_DIR',Path(d)):
                with self.assertRaises(h.Stop):h.durable_state_guard()
    def test_state_reject_world_readable(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'state.json'
            f.write_text(json.dumps({'sessions':{},'seen':{CANARY:5},'inflight':{}}))
            f.chmod(0o644)
            with patch.object(h,'STATE_DIR',Path(d)):
                with self.assertRaises(h.Stop):h.durable_state_guard()
    def test_bad_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'file';f.write_text('test');f.chmod(0o600)
            self.assertTrue(h.trusted_file(f,private=True))
            f.chmod(0o644)
            self.assertFalse(h.trusted_file(f,private=True))
    def test_reject_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'file';f.write_text('test')
            link=Path(d)/'link';link.symlink_to(f)
            self.assertFalse(h.trusted_file(link))
    def test_only_known_commands(self):
        self.assertEqual(h.main(['script','garbage']),2)

if __name__=='__main__': unittest.main()

import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from stage33f_agent_cli import (dispatch,Stop,CANARY,TASK_MID,REPORT_MID,ACK,CONSUMER,version_token)
import render_candidate33f as render

class FakeAgent:
    def __init__(self, seq=5, next_actor='agent'):
        self.seq=seq;self.next_actor=next_actor;self.claims=0;self.acks=0;self.writes=0
    def dialog_metadata(self,t):
        assert t==CANARY
        return {'state':'active','last_seq':self.seq,'version':self.seq,
                'title':'fixed canary','next_actor':self.next_actor}
    def claim_message(self,*args,**kwargs):
        self.claims+=1
        return {'claimed':True,'delivery_id':42,'lease_token':'1'*36}
    def delivery_status(self,t,seq):
        assert t==CANARY and seq==5
        return {'delivery_id':42,'status':'leased'}
    def ack_outbox(self,*args):
        self.acks+=1;return True
    def send_status(self,t,mid):return {'found':False}
    def send_report(self,t,mid,k,b,expected_version):
        self.writes+=1;return {'created':True,'seq':6,'version':6}

class Tests(unittest.TestCase):
    def test_version_token_known_formula(self):
        import hashlib
        self.assertEqual(version_token(CANARY,4),hashlib.sha1(f'pgbridge-v2\x00{CANARY}\x004'.encode()).hexdigest())
    def test_reject_wrong_task_before_read(self):
        with self.assertRaises(Stop):dispatch(SimpleNamespace(command='claim',task_id='task_'+'a'*32),FakeAgent())
    def test_reject_wrong_seq_before_mutation(self):
        a=FakeAgent()
        with self.assertRaises(Stop):dispatch(SimpleNamespace(command='claim',task_id=CANARY,seq=3,message_id=TASK_MID,consumer=CONSUMER),a)
        self.assertEqual(a.claims,0)
    def test_claim_specific_one_turn(self):
        a=FakeAgent()
        result=dispatch(SimpleNamespace(command='claim',task_id=CANARY,seq=5,message_id=TASK_MID,consumer=CONSUMER),a)
        self.assertTrue(result['claimed']);self.assertEqual(a.claims,1)
    def test_no_cross_ack(self):
        a=FakeAgent()
        with self.assertRaises(Stop):dispatch(SimpleNamespace(command='ack',task_id=CANARY,delivery_id=41,lease_token='1'*36,consumer=CONSUMER),a)
        self.assertEqual(a.acks,0)
    def test_ack_matching_receipt(self):
        a=FakeAgent()
        result=dispatch(SimpleNamespace(command='ack',task_id=CANARY,delivery_id=42,lease_token='1'*36,consumer=CONSUMER),a)
        self.assertTrue(result['acked']);self.assertEqual(a.acks,1)
    def test_append_only_exact_ack_and_turn(self):
        a=FakeAgent()
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'body.txt';p.write_text('wrong')
            args=SimpleNamespace(command='append',task_id=CANARY,kind='report',message_id=REPORT_MID,body_file=str(p),head=version_token(CANARY,5))
            with self.assertRaises(Stop):dispatch(args,a)
            self.assertEqual(a.writes,0)
            p.write_text(ACK+'\n')
            result=dispatch(args,a)
            self.assertEqual(result['seq'],6);self.assertEqual(a.writes,1)
    def test_append_wrong_version_denied(self):
        a=FakeAgent()
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'body.txt';p.write_text(ACK)
            args=SimpleNamespace(command='append',task_id=CANARY,kind='report',message_id=REPORT_MID,body_file=str(p),head=version_token(CANARY,4))
            with self.assertRaises(Stop):dispatch(args,a)
            self.assertEqual(a.writes,0)
    def test_cannot_create_dialog(self):
        with self.assertRaises(Stop):dispatch(SimpleNamespace(command='create',task_id=CANARY),FakeAgent())
    def test_candidate_render_hard_filters(self):
        raw='''const dryRun = true; // STAGE33E FORCED READ ONLY
const STATE_DIR = join(HOME, ".local/state/mt-pg-inbox");
const taskIds = Object.freeze(["task_f2c64cfdf7894537b35a19a6def977c6"]);
const x="/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py"; const y="/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py";
const z="--message-id <UNIQUE_ID>";
"Ответ публикуется в PostgreSQL, а НЕ в Git.",
// ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)
// state.inflight[key] op:"claim" op:"ack" session.prompt
'''
        result=render.render(raw)
        self.assertIn('const dryRun = false;',result)
        self.assertIn('mt-pg-inbox-agent-stage33f',result)
        self.assertNotIn('/home/kali/hermes-pg-stage33e-shadow-clients/actor_agent_cli.py',result)
        self.assertIn('/home/kali/hermes-pg-stage33f-sdk-preflight/stage33f_agent_cli.py',result)
        self.assertIn(REPORT_MID,result)
        self.assertIn(ACK,result)
    def test_candidate_reject_layout_change(self):
        with self.assertRaises(RuntimeError):render.render('irrelevant')
    def test_scripts_have_no_plugin_activation(self):
        s=Path('38_prepare_sdk_candidate.sh').read_text()
        for forbidden in ('systemctl --user restart','opencode.json','git push','pgv2-stage33f-sdk-task-v1'):
            self.assertNotIn(forbidden,s)
    def test_inventory_no_run_sdk(self):
        s=Path('inventory33f.py').read_text()
        for forbidden in ('session.prompt(', 'session.create(', 'cg.send_message(', 'ag.claim_message('):
            self.assertNotIn(forbidden,s)

if __name__=='__main__':unittest.main()

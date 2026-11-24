import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from logic33fb import CANARY, TASK_MID, REPORT_MID, BODY, ACK, PREV_ACK, send_once, status
from prepare_cutover33fb import draft_json, OLD, SRC

PREV = {'seq':4, 'message_id':'pgv2-tunnel-write-ack-20261122-v1',
        'sender':'agent','recipient':'chatgpt','kind':'report','body':PREV_ACK,'returned':False}
TASK = {'seq':5,'message_id':TASK_MID,'sender':'chatgpt','recipient':'agent','kind':'task','body':BODY,'returned':False}
REPORT = {'seq':6,'message_id':REPORT_MID,'sender':'agent','recipient':'chatgpt','kind':'report','body':ACK,'returned':False}

class FakeDB:
    def __init__(self,phase=4,status5='pending'):
        self.phase=phase;self.s5=status5;self.writes=0
    def dialog_metadata(self,tid):
        assert tid==CANARY
        return {'state':'active','last_seq':self.phase,
                'next_actor':'chatgpt' if self.phase in (4,6) else 'agent',
                'version':self.phase}
    def view_dialog(self,tid,offset,limit):
        assert tid==CANARY and offset==0 and limit==8
        rows=([REPORT] if self.phase==6 else [])+([TASK] if self.phase>=5 else [])+[PREV]
        return {'last_seq':self.phase,'messages':rows}
    def send_status(self,tid,mid):
        if mid==TASK_MID:return {'found':self.phase>=5,'seq':5 if self.phase>=5 else None,'sender':'chatgpt'}
        if mid==REPORT_MID:return {'found':self.phase>=6,'seq':6 if self.phase>=6 else None,'sender':'agent'}
        raise AssertionError('wrong id')
    def delivery_status(self,tid,seq):
        assert seq==5
        return {'status':self.s5}
    def send_message(self,tid,mid,kind,body,expected_version):
        assert (tid,mid,kind,body,expected_version)==(CANARY,TASK_MID,'task',BODY,4)
        self.writes+=1
        self.phase=5
        return {'created':True,'seq':5,'version':5}

class Tests(unittest.TestCase):
    def test_state_before_submission(self):
        x=FakeDB()
        s=status(x)
        self.assertEqual(s['seq'],4)
        self.assertFalse(s['test_task_committed'])
        self.assertFalse(s['sdk_roundtrip_complete'])
        self.assertEqual(x.writes,0)
    def test_send_one_fixed_task(self):
        x=FakeDB()
        r=send_once(x)
        self.assertEqual(r['send_seq'],5)
        self.assertFalse(r['already_committed'])
        self.assertEqual(x.writes,1)
    def test_retry_is_idempotent(self):
        x=FakeDB(5)
        self.assertTrue(send_once(x)['already_committed'])
        self.assertEqual(x.writes,0)
    def test_before_report_not_complete(self):
        x=FakeDB(5,'leased')
        self.assertFalse(status(x)['sdk_roundtrip_complete'])
    def test_report_and_ack_are_required(self):
        self.assertFalse(status(FakeDB(6,'leased'))['sdk_roundtrip_complete'])
        self.assertTrue(status(FakeDB(6,'delivered'))['sdk_roundtrip_complete'])
    def test_cannot_send_after_report(self):
        x=FakeDB(6)
        self.assertTrue(send_once(x)['already_committed'])
        self.assertEqual(x.writes,0)
    def test_reject_prior_canary_mismatch(self):
        class Bad(FakeDB):
            def view_dialog(self,*a):return {'last_seq':4,'messages':[dict(PREV,body='WRONG')]}
        with self.assertRaisesRegex(ValueError,'prior_canary_state_invalid'):status(Bad())
    def test_reject_task_collision(self):
        class Bad(FakeDB):
            def view_dialog(self,*a):return {'last_seq':5,'messages':[dict(TASK,body='WRONG'),PREV]}
        with self.assertRaisesRegex(ValueError,'task_idempotency_collision'):send_once(Bad(5))
    def test_reject_report_collision(self):
        class Bad(FakeDB):
            def view_dialog(self,*a):return {'last_seq':6,'messages':[dict(REPORT,body='WRONG'),TASK,PREV]}
        with self.assertRaisesRegex(ValueError,'report_idempotency_collision'):status(Bad(6))
    def test_refuses_out_of_order_metadata(self):
        class Bad(FakeDB):
            def dialog_metadata(self,*a):return dict(super().dialog_metadata(*a),next_actor='agent')
        with self.assertRaisesRegex(ValueError,'unexpected_dialog_turn'):send_once(Bad(4))
    def test_draft_replaces_only_exact_plugin(self):
        plugs=[[f'/old/other{i}.mjs',{}] for i in range(7)]
        plugs.append([OLD,{'dry_run':False,'task_ids':[CANARY],'tick_ms':30000}])
        src={'plugin':plugs,'other_setting':{'key':'value'}}
        output=json.loads(draft_json(json.dumps(src).encode()))
        self.assertEqual(output['plugin'][-1][0],str(SRC))
        self.assertEqual(output['plugin'][-1][1],plugs[-1][1])
        self.assertEqual(output['plugin'][:7],plugs[:7])
        self.assertEqual(output['other_setting'],src['other_setting'])
    def test_draft_rejects_multiple_active_plugins(self):
        plugs=[[OLD,{'dry_run':False,'task_ids':[CANARY]}] for i in range(8)]
        with self.assertRaisesRegex(RuntimeError,'active_canary_plugin_not_unique'):
            draft_json(json.dumps({'plugin':plugs}).encode())
    def test_scripts_do_not_touch_working_services(self):
        s=Path('39_install_mcp_only.sh').read_text()
        for bad in ('systemctl --user stop hermes-pg-tunnel-one-shot',
                    'systemctl --user restart', 'pgv2-stage33f-sdk-task-v1',
                    'cp opencode-stage33f-draft.json /home/kali/.config/gigatool/opencode.json'):
            self.assertNotIn(bad,s)
    def test_mcp_immutable_no_input(self):
        text=Path('cg_mcp33fb.py').read_text()
        for fn in ('stage33f_actor_health()', 'stage33f_canary_status()', 'stage33f_send_once()'):
            self.assertIn(fn,text)
        self.assertNotIn('bridge_app',text)
    def test_mcp_loopback_not_tunnel(self):
        text=Path('cg_mcp33fb.py').read_text()
        self.assertIn("HOST, PORT = '127.0.0.1', 18773",text)
        s=Path('39_install_mcp_only.sh').read_text()
        self.assertNotIn('tunnel-client',s)
        self.assertNotIn('MCP_SERVER_URL',s)
    def test_mcp_write_unarmed_by_default(self):
        from logic33fb import is_armed
        self.assertFalse(is_armed())
        text=Path('cg_mcp33fb.py').read_text()
        self.assertIn('if not is_armed():',text)
        self.assertIn('stage33fc_exclusive_dispatcher_not_armed',text)
    def test_actor_bound_and_no_sender_arg(self):
        text=Path('logic33fb.py').read_text()
        self.assertIn('expected_version=meta[\'version\']',text)
        self.assertNotIn('p_sender',text)
        self.assertNotIn('set role',text.lower())

if __name__=='__main__':unittest.main()

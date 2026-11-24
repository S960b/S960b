from __future__ import annotations
import json
import re
import unittest
from unittest.mock import patch, Mock
import stage34fc as s


def before():
    return {'detail':{'registered':True,'state':'active','dispatch_enabled':False,
                      'policy_version':0,'version':0,'last_seq':0,'next_actor':'chatgpt'},
            'task_status':{'found':False},'delivery1':None,'delivery2':None,
            'view':{'task_id':s.PILOT,'messages':[]},'queued':[], 'report_status':None}

def after(report=False, delivered=False, report_ack='pending'):
    n = 2 if report else 1
    msgs = [{'seq':1,'message_id':s.TASK_MID,'sender':'chatgpt','recipient':'agent',
             'kind':'task','body':s.TASK_BODY}]
    if report:
        msgs.append({'seq':2,'message_id':s.REPORT_MID,'sender':'agent',
                     'recipient':'chatgpt','kind':'report','body':s.REPORT_BODY})
    return {'detail':{'registered':True,'state':'active','dispatch_enabled':True,
                      'policy_version':1,'version':n,'last_seq':n,
                      'next_actor':'chatgpt' if report else 'agent'},
            'task_status':{'found':True,'seq':1,'sender':'chatgpt','kind':'task','message_id':s.TASK_MID},
            'delivery1':{'status':'delivered' if delivered else 'leased','delivery_id':10},
            'delivery2':{'status':report_ack,'delivery_id':11} if report else None,
            'view':{'task_id':s.PILOT,'messages':list(reversed(msgs))},
            'queued':[] if report else [{'task_id':s.PILOT}],
            'report_status':{'found':True,'seq':2,'sender':'agent','kind':'report'} if report else None}

class FakeCursor:
    def __init__(self, send_ok=True):
        self.calls=[]
        self.payload=None
        self.send_ok=send_ok
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self, sql, args=None):
        self.calls.append((sql,args))
        if 'session_user' in sql:
            self.payload={'actor':'bridge_chatgpt'}
        elif 'cg_set_managed_dispatch' in sql:
            self.payload={'r':{'task_id':s.PILOT,'dispatch_enabled':True,'changed':True,'policy_version':1}}
        elif 'cg_send_managed_task' in sql:
            if not self.send_ok: raise RuntimeError('injected_failure')
            self.payload={'r':{'task_id':s.PILOT,'message_id':s.TASK_MID,
                               'seq':1,'version':1,'created':True,
                               'sender':'chatgpt','recipient':'agent'}}
        elif 'cg_managed_detail' in sql:
            self.payload={'r':after()['detail']}
        elif 'cg_claim_managed_report' in sql:
            self.payload={'r':{'claimed':True,'delivery_id':11,
                               'lease_token':'11111111-1111-4111-8111-111111111111'}}
        elif 'cg_ack_managed_report' in sql:
            self.payload={'r':True}
        elif 'bridge.delivery_status' in sql:
            self.payload={'r':{'status':'delivered'}}
        else:
            self.payload=None
    def fetchone(self):return self.payload

class FakeConn:
    def __init__(self, send_ok=True):
        self.cursor_instance=FakeCursor(send_ok)
        self.committed=False
        self.rolled_back=False
    def __enter__(self):return self
    def __exit__(self,ty,ex,tb):
        if ty is None:self.committed=True
        else:self.rolled_back=True
        return False
    def cursor(self):return self.cursor_instance
class FakeActor:
    def __init__(self,conn):self.conn=conn
    def _connect(self):return self.conn

class ValidationTests(unittest.TestCase):
    def test_constants_and_canary_disjoint(self):
        self.assertRegex(s.PILOT,r'^task_[0-9a-f]{32}$')
        self.assertNotEqual(s.PILOT,s.CANARY)
        self.assertEqual(s.CONFIG_SHA,'4b32ac0fe83c902f5a7e2e5a749b104506987e1380aadc863ff15ca57b7786c3')
        self.assertEqual(s.PILOT_JS_SHA,'b3679086583d77c96b6ed1c3dc7b1ed1656707dabba4d3ac74843fe64177d730')
    def test_report_identifier_matches_js(self):
        self.assertEqual(s.REPORT_MID,'pgv2-mg-'+s.PILOT[5:]+'-1-report')
        self.assertLessEqual(len(s.REPORT_MID),64)
    def test_task_body_contains_exact_marker(self):
        self.assertEqual(s.TASK_BODY.count(s.REPORT_BODY),1)
        self.assertIn('chmod',s.TASK_BODY.replace('mode 0600','chmod 600'))
    def test_before_valid(self):s.validate_before(before())
    def test_before_refuses_prior_dispatch(self):
        x=before();x['detail']['dispatch_enabled']=True
        with self.assertRaises(s.Refused):s.validate_before(x)
    def test_before_refuses_prior_task(self):
        x=before();x['task_status']={'found':True,'seq':1}
        with self.assertRaises(s.Refused):s.validate_before(x)
    def test_after_task_only(self):s.validate_after(after())
    def test_after_report_verified(self):s.validate_after(after(report=True,delivered=True))
    def test_after_wrong_report_body_denied(self):
        x=after(report=True);x['view']['messages'][0]['body']='WRONG'
        with self.assertRaises(s.Refused):s.validate_after(x)
    def test_after_wrong_report_id_denied(self):
        x=after(report=True);x['view']['messages'][0]['message_id']='other'
        with self.assertRaises(s.Refused):s.validate_after(x)
    def test_after_missing_task_delivery_denied(self):
        x=after();x['delivery1']=None
        with self.assertRaises(s.Refused):s.validate_after(x)
    def test_after_other_task_denied(self):
        x=after();x['task_status']['message_id']='wrong'
        with self.assertRaises(s.Refused):s.validate_after(x)
    def test_ready_requires_report_and_delivered(self):
        self.assertFalse(s.report_ready(after()))
        self.assertFalse(s.report_ready(after(report=True)))
        self.assertTrue(s.report_ready(after(report=True,delivered=True)))
    def test_plugin_path_contract(self):
        self.assertEqual(s.plugin_path(['/a',{'dry_run':False}]),'/a')
        self.assertEqual(s.plugin_path('/a'),'/a')
        with self.assertRaises(s.Refused):s.plugin_path([12,{}])
    def test_write_gate_exact_bytes(self):
        self.assertEqual(s.ARM_CONTENT,b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n')
        self.assertEqual(s.GATE.name,'managed-writes-stage34d-armed')
    def test_cli_path_pinned(self):
        self.assertEqual(s.MANAGED_CLI_SHA,'bf8cad69a02e71a70ec3377504c24a3fc2cb71a38f2c8c4a025abc587b774485')

class TransactionTests(unittest.TestCase):
    def test_send_is_one_transaction(self):
        conn=FakeConn()
        with patch.object(s,'preflight'),patch.object(s,'record_no_replace'),\
             patch.object(s,'write_gate'),patch.object(s,'actors',return_value=(FakeActor(conn),None)),\
             patch.object(s,'read_db',return_value=after()):
            s.send_once()
        self.assertTrue(conn.committed)
        calls=[sql for sql,_ in conn.cursor_instance.calls]
        self.assertEqual(sum('cg_set_managed_dispatch' in q for q in calls),1)
        self.assertEqual(sum('cg_send_managed_task' in q for q in calls),1)
        self.assertLess(next(i for i,q in enumerate(calls) if 'cg_set_managed_dispatch' in q),
                        next(i for i,q in enumerate(calls) if 'cg_send_managed_task' in q))
    def test_send_failure_rolls_back_policy(self):
        conn=FakeConn(send_ok=False)
        with patch.object(s,'preflight'),patch.object(s,'record_no_replace'),\
             patch.object(s,'write_gate'),patch.object(s,'actors',return_value=(FakeActor(conn),None)),\
             patch.object(s,'read_db',return_value=after()):
            with self.assertRaises(RuntimeError):s.send_once()
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)
    def test_ack_report_is_one_transaction(self):
        conn=FakeConn()
        with patch.object(s,'pin_local'),\
             patch.object(s,'ACTIVATION_ATTEMPT',Mock(exists=Mock(return_value=True))),\
             patch.object(s,'REPORT_ACK_ATTEMPT',Mock(exists=Mock(return_value=False))),\
             patch.object(s,'read_db',return_value=after(report=True,delivered=True)),\
             patch.object(s,'trusted',return_value=True),\
             patch.object(s,'PILOT_STATE',Mock(read_text=Mock(return_value=json.dumps({'inflight':{},'seen':{s.PILOT:1}})))),\
             patch.object(s,'record_no_replace'),patch.object(s,'actors',return_value=(FakeActor(conn),None)),\
             patch.object(s,'verify'):
            s.ack_report_once()
        self.assertTrue(conn.committed)
        calls=[sql for sql,_ in conn.cursor_instance.calls]
        self.assertEqual(sum('cg_claim_managed_report' in q for q in calls),1)
        self.assertEqual(sum('cg_ack_managed_report' in q for q in calls),1)
    def test_no_write_api_outside_managed(self):
        source=__import__('inspect').getsource(s)
        self.assertNotIn('bridge.cg_send_message(',source)
        self.assertNotIn('bridge.ag_send_message(',source)
        self.assertNotIn('bridge_app',source.replace('No broad bridge_app access',''))
        self.assertNotIn('git clone',source)

if __name__=='__main__':unittest.main()

class FilesystemSafetyTests(unittest.TestCase):
    def test_gate_private_exclusive(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as name:
            gate=Path(name)/'armed'
            with patch.object(s,'GATE',gate):
                s.write_gate()
                self.assertEqual(gate.read_bytes(),s.ARM_CONTENT)
                self.assertEqual(gate.stat().st_mode & 0o777,0o600)
                with self.assertRaises(s.Refused):s.write_gate()
    def test_journal_private_no_replace(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as name:
            dest=Path(name)/'journal.json'
            with patch.object(s,'ROOT',Path(name)):
                s.record_no_replace(dest,{'attempted':True})
                self.assertEqual(json.loads(dest.read_text()),{'attempted':True})
                self.assertEqual(dest.stat().st_mode & 0o777,0o600)
                with self.assertRaises(s.Refused):s.record_no_replace(dest,{'attempted':False})
    def test_ack_does_not_claim_without_exact_report(self):
        x=after(report=True,delivered=True)
        x['view']['messages'][0]['body']='FORGED'
        with patch.object(s,'pin_local'),patch.object(s,'ACTIVATION_ATTEMPT',Mock(exists=Mock(return_value=True))),\
             patch.object(s,'REPORT_ACK_ATTEMPT',Mock(exists=Mock(return_value=False))),\
             patch.object(s,'read_db',return_value=x),\
             patch.object(s,'actors') as actor_mock:
            with self.assertRaises(s.Refused):s.ack_report_once()
            actor_mock.assert_called_once()

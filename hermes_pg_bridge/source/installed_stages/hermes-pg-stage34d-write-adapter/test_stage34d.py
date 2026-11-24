from __future__ import annotations
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import managed_agent_cli34d as cli
import managed_read34c as read
import render34d as render
import probe34d

TID='task_'+'a'*32
MID='test-message-001'
TOKEN='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'

class Agent:
    def __init__(self, results): self.results={k:list(v) for k,v in results.items()};self.calls=[]
    def _one(self,sql,params=()):
        self.calls.append((sql,params))
        for name,v in self.results.items():
            if 'bridge.'+name+'(' in sql:
                if not v:raise RuntimeError('no more simulated response')
                res=v.pop(0)
                if isinstance(res,BaseException):raise res
                return res
        raise RuntimeError('unexpected SQL '+sql)

def args(op,**kwargs):
    d=dict(command=op,task_id=TID,message_id=MID,seq=1,consumer=cli.CONSUMER,
           delivery_id=78,lease_token=TOKEN,kind='report',body_file=None,
           head=read.version_token(TID,1),offset=0,limit=10)
    d.update(kwargs)
    return SimpleNamespace(**d)

class Tests(unittest.TestCase):
    def test_read_source_pinned(self):
        from pathlib import Path
        self.assertEqual(cli.EXPECTED_READ_SHA,render.hashlib.sha256(Path(__file__).with_name('managed_read34c.py').read_bytes()).hexdigest())

    def test_no_write_without_arm_even_agent_valid(self):
        fake=Agent({})
        for op in ('claim','ack','append'):
            with self.assertRaisesRegex(cli.Denied,'stage34d_writes_not_armed'):
                cli.dispatch(args(op),fake)
        self.assertEqual(fake.calls,[])

    def test_claim_managed_only_sql(self):
        fake=Agent({'ag_claim_managed_message':[{'claimed':True,'delivery_id':78,'lease_token':TOKEN}]})
        out=cli.dispatch(args('claim'),fake,write_allowed=True)
        self.assertTrue(out['claimed']);self.assertEqual(len(fake.calls),1)
        self.assertIn('ag_claim_managed_message',fake.calls[0][0])
        self.assertEqual(fake.calls[0][1],(TID,1,MID,cli.CONSUMER,3600))

    def test_claim_wrong_consumer_is_rejected_without_sql(self):
        fake=Agent({})
        with self.assertRaisesRegex(cli.Denied,'wrong_consumer'):
            cli.dispatch(args('claim',consumer='other'),fake,write_allowed=True)
        self.assertEqual(fake.calls,[])

    def test_claim_incomplete_receipt_denied(self):
        fake=Agent({'ag_claim_managed_message':[{'claimed':True}]})
        with self.assertRaises(cli.Denied):cli.dispatch(args('claim'),fake,write_allowed=True)

    def test_ack_uses_only_managed_functions(self):
        fake=Agent({'ag_managed_delivery_status':[{'delivery_id':78,'status':'leased'}],
                    'ag_ack_managed_message':[True]})
        out=cli.dispatch(args('ack'),fake,write_allowed=True)
        self.assertEqual(out,{'acked':True,'already_delivered':False})
        self.assertEqual([('ag_managed_delivery_status' in q or 'ag_ack_managed_message' in q) for q,_ in fake.calls],[True,True])

    def test_ack_idempotent_after_pg_commit(self):
        fake=Agent({'ag_managed_delivery_status':[{'delivery_id':78,'status':'delivered'}]})
        self.assertTrue(cli.dispatch(args('ack'),fake,write_allowed=True)['already_delivered'])
        self.assertEqual(len(fake.calls),1)

    def test_ack_lost_response_checks_db(self):
        fake=Agent({'ag_managed_delivery_status':[{'delivery_id':78,'status':'leased'},
                                                   {'delivery_id':78,'status':'delivered'}],
                    'ag_ack_managed_message':[RuntimeError('lost network reply')]})
        self.assertEqual(cli.dispatch(args('ack'),fake,write_allowed=True),
                         {'acked':True,'already_delivered':True})
        self.assertEqual(len(fake.calls),3)

    def test_ack_hold_on_unconfirmed(self):
        fake=Agent({'ag_managed_delivery_status':[{'delivery_id':78,'status':'leased'},
                                                   {'delivery_id':78,'status':'leased'}],
                    'ag_ack_managed_message':[False]})
        with self.assertRaisesRegex(cli.Denied,'ack_unconfirmed_hold_inflight'):
            cli.dispatch(args('ack'),fake,write_allowed=True)

    def test_ack_wrong_delivery_never_called(self):
        fake=Agent({'ag_managed_delivery_status':[{'delivery_id':99,'status':'leased'}]})
        with self.assertRaisesRegex(cli.Denied,'ack_wrong_delivery'):
            cli.dispatch(args('ack'),fake,write_allowed=True)
        self.assertEqual(len(fake.calls),1)

    def test_ack_wrong_token_never_called(self):
        fake=Agent({})
        with self.assertRaisesRegex(cli.Denied,'ack_args_invalid'):
            cli.dispatch(args('ack',lease_token='not-a-token'),fake,write_allowed=True)
        self.assertEqual(fake.calls,[])

    def test_append_requires_report_kind(self):
        fake=Agent({})
        with self.assertRaisesRegex(cli.Denied,'only_report_allowed'):
            cli.dispatch(args('append',kind='task'),fake,write_allowed=True)

    def test_append_enforces_optimistic_version(self):
        fake=Agent({'ag_view_managed_dialog':[{'task_id':TID,'state':'active',
             'next_actor':'agent','version':1}],
             'ag_send_managed_report':[{'seq':2,'created':True,'version':2}]})
        with patch.object(cli,'guarded_body',return_value='hello'):
            r=cli.dispatch(args('append',body_file='/home/kali/answer.txt'),fake,write_allowed=True)
        self.assertEqual(r['seq'],2)
        self.assertEqual(len(fake.calls),2)
        self.assertIn('ag_send_managed_report',fake.calls[-1][0])
        self.assertEqual(fake.calls[-1][1],(TID,MID,'hello',1))

    def test_append_stale_head_does_not_send(self):
        fake=Agent({'ag_view_managed_dialog':[{'task_id':TID,'state':'active',
             'next_actor':'agent','version':2}]})
        with patch.object(cli,'guarded_body',return_value='hello'):
            with self.assertRaisesRegex(cli.Denied,'stale_head'):
                cli.dispatch(args('append',body_file='/home/kali/answer.txt'),fake,write_allowed=True)
        self.assertEqual(len(fake.calls),1)

    def test_append_no_agent_turn_does_not_send(self):
        fake=Agent({'ag_view_managed_dialog':[{'task_id':TID,'state':'active',
             'next_actor':'chatgpt','version':1}]})
        with patch.object(cli,'guarded_body',return_value='hello'):
            with self.assertRaisesRegex(cli.Denied,'agent_turn_not_active'):
                cli.dispatch(args('append',body_file='/home/kali/answer.txt'),fake,write_allowed=True)

    def test_read_operations_delegate_to_managed_only_cli(self):
        fake=Agent({'ag_dispatch_list':[[]]})
        result=cli.dispatch(args('list'),fake,write_allowed=False)
        self.assertEqual(result['dialogs'],[])
        self.assertEqual(len(fake.calls),1)
        self.assertIn('ag_dispatch_list',fake.calls[0][0])

    def test_renderer_changes_only_paths_and_state_and_stays_dryrun(self):
        src='\n'.join([render.OLD_CLI,render.OLD_CLI,render.OLD_STATE,render.OLD_DRY,
                      'const taskIds = null;',
                      'ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)',
                      'inflight_reconciled_ack_confirmed','session.prompt('])
        out=render.render(src)
        self.assertIn('const dryRun = true;',out)
        self.assertIn('mt-pg-inbox-managed-stage34d',out)
        self.assertEqual(out.count(str(render.CLI)),2)
        self.assertNotIn(render.OLD_CLI,out)

    def test_renderer_rejects_source_drift(self):
        with self.assertRaises(render.Refused):render.render('some changed code')

    def test_legacy_broad_sql_never_called_from_write_cli(self):
        from pathlib import Path
        text=Path(__file__).with_name('managed_agent_cli34d.py').read_text()
        forbidden=('SELECT bridge.ack_outbox(', 'SELECT bridge.ag_send_message(',
                   'SELECT bridge.ag_claim_message(', 'SELECT bridge.claim_message(',
                   'SELECT bridge.messages ', 'SET ROLE bridge_app')
        for s in forbidden:self.assertNotIn(s,text)

    def test_ack_and_claim_client_no_mcp_or_sdk(self):
        from pathlib import Path
        text=Path(__file__).with_name('managed_agent_cli34d.py').read_text()
        for s in ('session.prompt','session.create','MCP_SERVER_URL','tunnel-client'):
            self.assertNotIn(s,text)

    def test_write_arm_not_in_package(self):
        from pathlib import Path
        self.assertNotIn('managed-writes-stage34d-armed',
             '\n'.join(p.name for p in Path(__file__).parent.iterdir()))

    def test_excerpts_have_exact_five_sections(self):
        names=('function buildPrompt(', 'async function dispatch(',
             'async function reconcileAnsweredInflight(',
             'export async function runTick(', 'function confirmedMtResponseAfter(')
        src='\n'.join(name+'\n  return true;\n}' for name in names)
        out=render.excerpts(src)
        self.assertEqual(len(out),5)
        self.assertTrue(all(x['sha256'] for x in out))

    def test_offline_scripts_do_not_mutate_live_services(self):
        from pathlib import Path
        for f in Path(__file__).parent.glob('*.sh'):
            s=f.read_text()
            for b in ('systemctl --user restart multitool', 'systemctl --user stop',
                      'cp -- "$PROPOSED" "$CFG"', 'DROP TABLE', 'ALTER ROLE'):
                self.assertNotIn(b,s)

if __name__=='__main__':unittest.main()

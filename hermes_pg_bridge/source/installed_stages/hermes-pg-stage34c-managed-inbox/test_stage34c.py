from __future__ import annotations
import hashlib
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import managed_agent_cli34c as cli
import render34c

ROOT=pathlib.Path(__file__).resolve().parent
SQL=(ROOT/'sql/009_managed_agent_read.sql').read_text('utf8')

class Agent:
    def __init__(self, results): self.results=results; self.calls=[]
    def _one(self,sql,args=()):
        self.calls.append((sql,args))
        for name,val in self.results.items():
            if 'bridge.'+name+'(' in sql:return val
        raise RuntimeError('unsafe query')

class ContractTests(unittest.TestCase):
    def test_sql_transaction_one_shot(self):
        self.assertIn('BEGIN;',SQL)
        self.assertIn('COMMIT;',SQL)
        self.assertIn('EXPECTED_39_FUNCTIONS',SQL)
        self.assertIn('STAGE34C_MANAGED_AGENT_READ_SQL_COMMITTED',SQL)
        self.assertNotIn('DROP TABLE',SQL)
    def test_three_only_narrow_functions(self):
        self.assertEqual(SQL.count('CREATE FUNCTION bridge.ag_'),3)
        for k in ('ag_view_managed_dialog','ag_managed_delivery_status','ag_managed_report_status'):
            self.assertIn('bridge.'+k,SQL)
        self.assertIn('IF n<>42',SQL)
    def test_role_guard_and_acl(self):
        self.assertEqual(SQL.count("session_user::text<>'bridge_agent'"),3)
        self.assertIn('FROM PUBLIC,bridge_app,bridge_agent,bridge_chatgpt',SQL)
        self.assertIn('TO bridge_agent',SQL)
        self.assertIn('activated_at IS NOT NULL',SQL)
    def test_no_inline_credentials_or_api_exposure(self):
        for bad in ('PGPASSWORD=', 'MCP_SERVER_URL','PUBLIC ACCESS','SET ROLE bridge_agent','GRANT SELECT ON bridge.'):
            self.assertNotIn(bad,SQL)
    def test_renderer_exact_source_contract(self):
        s='\n'.join([render34c.OLD_CLI,render34c.OLD_CLI,
                render34c.OLD_FILTER,render34c.OLD_DRY,render34c.OLD_STATE,
                'ctx.taskIds && !ctx.taskIds.includes(dialog.task_id)',
                'if (ctx.dryRun)', 'session.prompt(',
                'inflight_reconciled_ack_confirmed'])
        out=render34c.render(s)
        self.assertNotIn(render34c.OLD_CLI,out)
        self.assertIn('const dryRun = true;',out)
        self.assertIn('const taskIds = null;',out)
        self.assertIn(str(render34c.CLI),out)
        self.assertIn('mt-pg-inbox-managed-stage34c',out)
    def test_renderer_fail_closed_on_drift(self):
        with self.assertRaises(render34c.Stop):render34c.render('arbitrary source')
    def test_renderer_old_CLI_count_must_be_exact(self):
        s=render34c.OLD_CLI+'\n'+render34c.OLD_FILTER+'\n'+render34c.OLD_DRY+'\n'+render34c.OLD_STATE
        with self.assertRaises(render34c.Stop):render34c.render(s)
    def test_write_ops_denied_without_db_call(self):
        a=Agent({})
        for op in cli.DENIED:
            with self.assertRaises(cli.Refused):cli.dispatch(SimpleNamespace(command=op),a)
        self.assertEqual(a.calls,[])
    def test_list_managed_empty(self):
        a=Agent({'ag_dispatch_list':[]})
        result=cli.dispatch(cli.parse(['list','--offset','0','--limit','20']),a)
        self.assertEqual(result['dialogs'],[])
        self.assertEqual(result['next_offset'],None)
        self.assertEqual(len(a.calls),1)
        self.assertIn('ag_dispatch_list',a.calls[0][0])
    def test_list_rejects_bad_row(self):
        a=Agent({'ag_dispatch_list':[{'task_id':'task_bad','next_actor':'agent'}]})
        with self.assertRaises(cli.Refused):cli.dispatch(cli.parse(['list']),a)
    def test_list_pagination(self):
        tid='task_'+'b'*32
        a=Agent({'ag_dispatch_list':[{'task_id':tid,'next_actor':'agent','title':'Test'}]})
        res=cli.dispatch(cli.parse(['list','--limit','1']),a)
        self.assertEqual(res['next_offset'],1)
        self.assertEqual(res['dialogs'][0]['task_id'],tid)
    def test_peek_uses_only_managed_view(self):
        tid='task_'+'b'*32
        a=Agent({'ag_view_managed_dialog':{'task_id':tid,'state':'active','title':'t',
            'version':1,'last_seq':1,'next_actor':'agent','messages':[{
            'message_id':'safe','seq':1,'sender':'chatgpt','recipient':'agent',
            'body':'hello','kind':'task','returned':False,'created_at':None}]}})
        res=cli.dispatch(cli.parse(['peek','--task-id',tid]),a)
        self.assertEqual(res['history'][0]['body'],'hello')
        self.assertEqual(res['waiting_for'],'mt')
        self.assertEqual(res['seq'],1)
        self.assertTrue(all('ag_view_managed_dialog' in v[0] for v in a.calls))
    def test_read_uses_managed_exact_selector(self):
        tid='task_'+'c'*32
        a=Agent({'ag_read_managed_message':{'task_id':tid,'seq':7,'message_id':'m7',
          'sender':'chatgpt','recipient':'agent','body':'safe','kind':'task','created_at':None}})
        res=cli.dispatch(cli.parse(['read','--task-id',tid,'--seq','7','--message-id','m7']),a)
        self.assertEqual(res['seq'],7)
        self.assertTrue('ag_read_managed_message' in a.calls[0][0])
    def test_read_wrong_sender_refused(self):
        tid='task_'+'c'*32
        a=Agent({'ag_read_managed_message':{'seq':7,'message_id':'m7','sender':'agent','recipient':'chatgpt'}})
        with self.assertRaises(cli.Refused):
            cli.dispatch(cli.parse(['read','--task-id',tid,'--seq','7','--message-id','m7']),a)
    def test_delivery_scoped_function(self):
        tid='task_'+'d'*32
        a=Agent({'ag_managed_delivery_status':{'status':'leased','delivery_id':42}})
        res=cli.dispatch(cli.parse(['delivery','--task-id',tid,'--seq','1']),a)
        self.assertEqual(res['status'],'leased')
        self.assertIn('ag_managed_delivery_status',a.calls[0][0])
    def test_report_status_scoped(self):
        tid='task_'+'d'*32
        a=Agent({'ag_managed_report_status':{'found':True,'sender':'agent','kind':'report','seq':2}})
        result=cli.dispatch(cli.parse(['status','--task-id',tid,'--message-id','id2']),a)
        self.assertEqual(result['confirmed_seq'],2)
    def test_report_status_not_found(self):
        tid='task_'+'d'*32
        a=Agent({'ag_managed_report_status':{'found':False}})
        result=cli.dispatch(cli.parse(['status','--task-id',tid,'--message-id','id2']),a)
        self.assertFalse(result['attempted'])
    def test_invalid_selector_no_db_call(self):
        a=Agent({})
        with self.assertRaises(cli.Refused):
            cli.dispatch(cli.parse(['read','--task-id','task_bad','--seq','1','--message-id','safe']),a)
        self.assertEqual(a.calls,[])
    def test_cli_only_managed_sql(self):
        s=(ROOT/'managed_agent_cli34c.py').read_text()
        for bad in ('SELECT bridge.view_dialog(', 'SELECT bridge.get_message(',
                    'SELECT bridge.claim_message(', 'SELECT bridge.ag_claim_message(',
                    'SELECT bridge.ag_send_message(', 'SELECT bridge.ack_outbox('):
            self.assertNotIn(bad,s)
        self.assertIn('stage34c_mutations_disabled',s)
    def test_no_live_cutover_in_scripts(self):
        for f in ROOT.glob('*.sh'):
            text=f.read_text()
            self.assertNotIn('systemctl --user restart multitool',text)
            self.assertNotIn('systemctl --user stop hermes-pg-tunnel',text)
            self.assertNotIn('cp -- "$PROPOSED" "$CFG"',text)

if __name__=='__main__':unittest.main()

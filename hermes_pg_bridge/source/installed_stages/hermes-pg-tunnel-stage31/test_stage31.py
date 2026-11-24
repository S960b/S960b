import ast
import unittest
from pathlib import Path
from logic31 import (CANARY_ID, CANARY_PREVIOUS_ACK, WRITE_MESSAGE_ID,
                     WRITE_BODY, WRITE_ACK, start_state, message_is_ours, compact_status)


def baseline():
    return {'task_id': CANARY_ID, 'total_messages': 2, 'waiting_for': 'chatgpt',
            'history': [{'seq': 2, 'from': 'mt', 'kind': 'report', 'body': CANARY_PREVIOUS_ACK},
                        {'seq': 1, 'from': 'chatgpt', 'kind': 'task', 'body': 'original'}]}


class FakeStore:
    def __init__(self): self.sent = False
    def health(self): return {'ok':True}
    def send_status(self,task,mid):
        assert task==CANARY_ID and mid==WRITE_MESSAGE_ID
        return {'found':self.sent, 'seq':3} if self.sent else {'found':False}


class FakeCat:
    def __init__(self): self.store = FakeStore(); self.calls=[]; self.pre = baseline()
    def view(self,task,offset,limit,order):
        assert task == CANARY_ID
        return self.pre
    def read_message(self,task,seq,mid):
        assert seq==3
        return {'message_id':mid,'from':'chatgpt','kind':'task','body':WRITE_BODY}
    def send(self,actor,task,mid,kind,body):
        assert (actor,task,mid,kind,body)==('chatgpt',CANARY_ID,WRITE_MESSAGE_ID,'task',WRITE_BODY)
        self.calls.append((actor,task,mid,kind,body))
        self.store.sent=True
        return {'seq':3,'created':True}


class CanaryTest(unittest.TestCase):
    def test_baseline_only(self):
        self.assertTrue(start_state(baseline()))
        cases = [dict(total_messages=3), dict(waiting_for='mt'), dict(task_id='task_'+'0'*32),
                 dict(history=[{'seq':2,'from':'mt','kind':'report','body':'fake'}]), dict(history=[])]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(start_state(baseline()|case))

    def test_exact_body_and_fixed_id(self):
        good={'message_id':WRITE_MESSAGE_ID,'from':'chatgpt','kind':'task','body':WRITE_BODY}
        self.assertTrue(message_is_ours(good))
        for key,val in [('from','mt'),('kind','report'),('message_id','different'),('body','different')]:
            self.assertFalse(message_is_ours(good|{key:val}))

    def test_one_real_write_simulation_and_retry(self):
        # Mock imports so no MCP/PG dependencies or network connection needed.
        import sys, types
        from unittest.mock import patch
        mcp=types.ModuleType('mcp'); mcp_server=types.ModuleType('mcp.server'); mcp_sub=types.ModuleType('mcp.server.mcpserver')
        mcp_sub.MCPServer=object
        security=types.ModuleType('mcp.server.transport_security'); security.TransportSecuritySettings=object
        types_mod=types.ModuleType('mcp.types'); types_mod.ToolAnnotations=object
        starlette=types.ModuleType('starlette'); req=types.ModuleType('starlette.requests'); req.Request=object
        res=types.ModuleType('starlette.responses'); res.JSONResponse=object
        pg=types.ModuleType('bridge_pg'); comp=types.ModuleType('bridge_pg.compat'); comp.Catalog=object
        store=types.ModuleType('bridge_pg.store'); store.Store=object; store.BridgeError=Exception
        mods={'mcp':mcp,'mcp.server':mcp_server,'mcp.server.mcpserver':mcp_sub,
              'mcp.server.transport_security':security,'mcp.types':types_mod,
              'starlette':starlette,'starlette.requests':req,'starlette.responses':res,
              'bridge_pg':pg,'bridge_pg.compat':comp,'bridge_pg.store':store}
        with patch.dict(sys.modules, mods):
            from importlib.util import spec_from_file_location, module_from_spec
            spec=spec_from_file_location('test_only_canary_server',Path(__file__).with_name('canary_server.py'))
            module=module_from_spec(spec); spec.loader.exec_module(module)
            c=FakeCat()
            out=module.send_once(c)
            self.assertTrue(out['ok']); self.assertFalse(out['already_committed'])
            self.assertEqual(len(c.calls),1)
            again=module.send_once(c)
            self.assertTrue(again['ok']); self.assertTrue(again['already_committed'])
            self.assertEqual(len(c.calls),1)
            c2=FakeCat(); c2.pre['waiting_for']='mt'
            refused=module.send_once(c2)
            self.assertEqual(refused['code'],'canary_precondition_failed')
            self.assertEqual(len(c2.calls),0)

    def test_status_minimized(self):
        v=baseline() | {'total_messages':4,'history':[{'seq':4,'from':'mt','kind':'report','body':WRITE_ACK},
            {'seq':3,'from':'chatgpt','kind':'task','body':WRITE_BODY}, {'seq':2,'from':'mt','kind':'report','body':CANARY_PREVIOUS_ACK}]}
        d=compact_status(v, {'found':True,'seq':3})
        self.assertTrue(d['roundtrip_complete'])
        self.assertEqual(d['send_seq'],3)
        self.assertNotIn('history',d)
        self.assertNotIn('body',str(d))

    def test_source_has_no_unbounded_tools(self):
        src=Path(__file__).with_name('canary_server.py').read_text()
        tree=ast.parse(src)
        funcs=[n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
        self.assertIn('pg_canary_send_once',funcs)
        self.assertFalse(any(x in funcs for x in ('dialog_create','dialog_send','execute','sql_query')))
        self.assertNotIn('subprocess',src)
        self.assertNotIn('os.system',src)
        self.assertNotIn('eval(',src)
        self.assertNotIn('exec(',src)


if __name__=='__main__':
    unittest.main(verbosity=2)

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOTDIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOTDIR))
import policy35v as policy

def module(file,name):
    spec=importlib.util.spec_from_file_location(name,file)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

cli=module(ROOTDIR/'__main__.py','stage35v_cli')
guardfile=ROOTDIR/'tests/guard35g.py'

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key=Path(self.tmp.name)/'dummy'
        self.key.write_bytes(b'Q'*64)
        self.key.chmod(0o600)
        self.names=[]
        self.inner=[]
        self.guard=module(guardfile,'gate_test')
    def setup_gate(self,methods=(),recorder=None):
        policy.install(self.guard,methods,self.names.append if recorder is None else recorder)
        async def app(scope,receive,send):
            self.inner.append(await receive())
            await send({'type':'http.response.start','status':204,'headers':[]})
            await send({'type':'http.response.body','body':b''})
        self.app=self.guard.MCPStaticGate(app,self.key)
    def request(self,body,headers=None):
        if not isinstance(body,bytes):body=json.dumps(body).encode()
        headers=[(self.guard.HEADER,b'Q'*64)] if headers is None else headers
        out=[]
        messages=iter([{'type':'http.request','body':body[:11],'more_body':True},
                       {'type':'http.request','body':body[11:],'more_body':False}])
        async def receive():return next(messages)
        async def send(m):out.append(m)
        asyncio.run(self.app({'type':'http','path':'/mcp','method':'POST','headers':headers},receive,send))
        return out[0]['status']
    def test_known(self):
        self.setup_gate()
        self.assertEqual(self.request({'method':'tools/list'}),204)
        self.assertEqual(self.names,[])
    def test_capture_exact(self):
        self.setup_gate()
        self.assertEqual(self.request({'method':'vendor/checkRegistration','params':{'secret':'not-in-marker'}}),403)
        self.assertEqual(self.names,['vendor/checkRegistration'])
        self.assertFalse(self.inner)
    def test_auth(self):
        self.setup_gate()
        for h in ([],[(self.guard.HEADER,b'incorrect')],[(self.guard.HEADER,b'Q'*64)]*2):
            self.assertEqual(self.request({'method':'server/discover'},h),403)
        self.assertEqual(self.names,[])
    def test_tool_controls(self):
        self.setup_gate(('server/discover',))
        for p in ({'name':'unknown'},None):
            self.assertEqual(self.request({'method':'tools/call','params':p}),403)
        self.assertEqual(self.names,[])
    def test_observed_only(self):
        self.setup_gate(('server/discover',))
        self.assertEqual(self.request({'method':'server/discover'}),204)
        self.assertEqual(self.request({'method':'skills/list'}),403)
        self.assertEqual(self.names,['skills/list'])
    def test_envelopes(self):
        self.setup_gate()
        for v in ([{'method':'any'}], {'method':None}, {'result':{}}, b'bad', b' '*131073):
            self.assertEqual(self.request(v),403)
        self.assertEqual(self.names,[])
    def test_write_failure_stays_denied(self):
        def fail(_):raise OSError('private text')
        self.setup_gate(recorder=fail)
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(self.request({'method':'unknown'}),403)
        self.assertNotIn('private text',captured.getvalue())
    def test_unreviewed_refused(self):
        with self.assertRaises(ValueError):policy.install(self.guard,['tools/call/new'])

class EvidenceTests(unittest.TestCase):
    def test_promote_only_observed_reviewed(self):
        self.assertEqual(policy.reviewed_subset(['server/discover']*3),('server/discover',))
        for rows in ([],['server/discover','vendor/get'],['[redacted]'],['tools/call']):
            with self.assertRaises(ValueError):policy.reviewed_subset(rows)
    def test_format(self):
        for name in ('params=value','abc\ndef','https://remote.invalid','A'*64,'methods/'+('a'*33),''):
            self.assertEqual(policy.safe_label(name),'[redacted]')
    def test_file_protection_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'events'
            with patch.object(policy,'EVENTS',p):
                policy.write_denial('vendor/privateMethod')
                policy.write_denial('foo\nPASSWORD')
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertEqual(policy.denials(p),['vendor/privateMethod','[redacted]'])
            with patch.object(cli,'denials',return_value=policy.denials(p)),contextlib.redirect_stdout(io.StringIO()) as out:
                cli.show()
            self.assertNotIn('privateMethod',out.getvalue())
            p.chmod(0o644)
            with self.assertRaises(ValueError):policy.denials(p)
    def test_no_arbitrary_override(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config';token='ab'*8
            cli.write_new(p,cli.override(token,('skills/list',)))
            self.assertEqual(cli.read_override(p),(token,('skills/list',)))
            p.write_bytes(cli.override(token,('anything/get',)))
            with self.assertRaises(ValueError):cli.read_override(p)
    def test_exclusive_install(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config'
            cli.write_new(p,b'first')
            with self.assertRaises(FileExistsError):cli.write_new(p,b'second')
            self.assertEqual(p.read_bytes(),b'first')
    def test_expired_old_timer_does_not_revert_new_trial(self):
        with patch.object(cli,'read_override',side_effect=[('bb'*8,()),None]),patch.object(cli,'proc_args',return_value=[]),patch.object(cli,'ctl') as ctl:
            cli.rollback('aa'*8)
            ctl.assert_not_called()
    def test_timer_ignores_committed(self):
        with patch.object(cli,'read_override',side_effect=[None,('ab'*8,('server/discover',))]),patch.object(cli,'proc_args',return_value=[]),patch.object(cli,'ctl') as ctl:
            cli.rollback('ab'*8)
            ctl.assert_not_called()
    def test_trial_with_unreviewed_never_mutates_service(self):
        with patch.object(cli,'read_override',return_value=None),patch.object(cli,'base_state',return_value='123'),patch.object(cli,'service_ok',return_value=True),patch.object(cli,'denials',return_value=['vendor/privateMethod']),patch.object(cli,'run') as cmd:
            with self.assertRaisesRegex(ValueError,'exact_method_requires_review'):cli.activate('trial')
            cmd.assert_not_called()
    def test_activation_failure_calls_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.conf'
            with patch.object(cli,'TEMP',p),patch.object(cli,'PERMANENT',Path(d)/'p.conf'),patch.object(cli,'EVENTS',Path(d)/'events'),patch.object(cli,'base_state',return_value='23'),patch.object(cli,'service_ok',return_value=True),patch.object(cli,'smoke'),patch.object(cli,'run'),patch.object(cli,'ctl'),patch.object(cli,'prop',return_value='active'),patch.object(cli,'rollback') as revert:
                with self.assertRaisesRegex(ValueError,'override_not_effective'):cli.activate('capture')
                revert.assert_called_once()

class TrialTests(unittest.TestCase):
    def test_new_denial_blocks_commit(self):
        with patch.object(cli,'read_override',side_effect=[('ab'*8,('server/discover',)),None]),patch.object(cli,'denials',return_value=['server/discover','vendor/registration']),patch.object(cli,'write_new') as put:
            with self.assertRaises(ValueError):cli.commit()
            put.assert_not_called()
    def test_wrong_host_smoke_rolls_back_trial(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.conf'
            token='ab'*8
            def prop(unit,name):
                return 'active' if name=='ActiveState' else f'{cli.ZIP} serve {token} server/discover'
            with patch.object(cli,'TEMP',p),patch.object(cli,'PERMANENT',Path(d)/'p.conf'),patch.object(cli,'denials',return_value=['server/discover']),patch.object(cli,'base_state',return_value='23'),patch.object(cli,'service_ok',return_value=True),patch.object(cli,'wait_service'),patch.object(cli,'smoke',side_effect=[None,ValueError('unsupported')]),patch.object(cli,'run'),patch.object(cli,'ctl'),patch.object(cli,'prop',side_effect=prop),patch.object(cli.secrets,'token_hex',return_value=token),patch.object(cli,'rollback') as revert:
                with self.assertRaisesRegex(ValueError,'unsupported'):cli.activate('trial')
                revert.assert_called_once_with(expected=token)
    def test_full_capture_cannot_promote(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'events'
            with patch.object(policy,'EVENTS',p):
                while not p.exists() or p.stat().st_size < 64000:policy.write_denial('server/discover')
                policy.write_denial('server/discover')
                size=p.stat().st_size
                policy.write_denial('server/discover')
                self.assertEqual(size,p.stat().st_size)
            with self.assertRaises(ValueError):policy.reviewed_subset(policy.denials(p))

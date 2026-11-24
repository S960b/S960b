"""Offline tests for 35J/K. All network and systemd calls in tests are mocked."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch, Mock
import stage35jk as s

class Rules(unittest.TestCase):
    def test_tools_exactly_11(self):
        self.assertEqual(len(s.EXPECTED_TOOLS),11)
        self.assertIn('managed_health',s.EXPECTED_TOOLS)
        self.assertIn('managed_list',s.EXPECTED_TOOLS)
        self.assertIn('managed_ack_report',s.EXPECTED_TOOLS)
    def test_local_endpoints(self):
        t=Path(__file__).parent/'run_managed_tunnel.sh'
        data=t.read_text()
        self.assertIn('http://127.0.0.1:18777/mcp',data)
        self.assertIn('127.0.0.1:18778',data)
        self.assertIn('X-Bridge-Local-Key: file:/home/kali/.config',data)
        self.assertIn('MCP_OAUTH_DISCOVERY=',data)
        self.assertIn('tunnel_6b02fe0b07f4819194b5adf2ff5e6a6a',data)
    def test_no_automatic_dispatch(self):
        source=Path(s.__file__).read_text()
        self.assertNotIn("'managed_set_dispatch'\n",source)
        self.assertNotIn('managed_send_task\'','\n'.join(source.split('for name,args,rid in',1)[1].split('mark(\'LOCAL_REAL_CLIENT_PG_READS',1)[0]))
        self.assertNotIn('armed.json',source)
    def test_systemd_not_enabled(self):
        self.assertNotIn('WantedBy=',s.UNIT_TEXT)
        self.assertIn('Restart=on-failure',s.UNIT_TEXT)
        self.assertIn('UMask=0077',s.UNIT_TEXT)
        self.assertIn('ExecStart=/home/kali/hermes-pg-stage35jk-cutover',s.UNIT_TEXT)
    def test_known_original_pins(self):
        self.assertEqual(len(s.CFG_SHA),64)
        self.assertEqual(len(s.OLD_SCRIPT_SHA),64)
        self.assertEqual(len(s.HI_SOURCE_SHA),64)
        self.assertEqual(len(s.G_SOURCE_SHA),64)
        self.assertEqual(len(s.HI_CAND_SHA),64)
    def test_package_expected_file_count(self):
        self.assertEqual(len(s.SRC_FILES),8)
        self.assertEqual({x.name for x in Path(__file__).parent.iterdir()},s.SRC_FILES)
    def test_no_mutation_of_existing_tunnel_or_plugin(self):
        data=(Path(__file__).parent/'180_install_verify_and_cutover_once.sh').read_text()
        self.assertNotIn('enable --now',data)
        self.assertNotIn('chmod -R',data)
        self.assertNotIn('rm -rf /home/kali',data)
        self.assertIn('local-once',data)
        self.assertIn('cutover-once',data)
    def test_one_shot_state(self):
        self.assertEqual(s.DEV_MARK.name,'stage35j-attempt.json')
        self.assertEqual(s.CUT_MARK.name,'stage35k-cutover-attempt.json')

class TrustedFiles(unittest.TestCase):
    def test_hash_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'file'
            f.write_bytes(b'test')
            f.chmod(0o600)
            expect=hashlib.sha256(b'test').hexdigest()
            self.assertTrue(s.private_file(f,expect))
            self.assertFalse(s.private_file(f,'0'*64))
            f.chmod(0o644)
            self.assertFalse(s.private_file(f))
            self.assertTrue(s.private_file(f,allow_world_read=True))
    def test_symlink_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'file';f.write_bytes(b'ok');f.chmod(0o600)
            l=Path(tmp)/'link';l.symlink_to(f)
            self.assertFalse(s.private_file(l))
    def test_hardlink_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'file';f.write_bytes(b'ok');f.chmod(0o600)
            os.link(f,Path(tmp)/'link')
            self.assertFalse(s.private_file(f))
    def test_marker_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'attempt'
            s.private_marker(p,{'secret':False})
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
            with self.assertRaises(FileExistsError):s.private_marker(p,{'again':True})
            self.assertEqual(s.read_marker(p),{'secret':False})
    def test_marker_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'attempt';p.write_text('[]');p.chmod(0o600)
            with self.assertRaises(s.Stop):s.read_marker(p)

class SessionCleanup(unittest.TestCase):
    def test_no_session(self):
        self.assertEqual(s.cleanup_status(12345,'/mcp',None,'2025-06-18'),'NO_SESSION')
    def check_status(self,code):
        res=Mock(status=code)
        con=Mock()
        con.getresponse.return_value=res
        with patch.object(s.http.client,'HTTPConnection',return_value=con):
            status=s.cleanup_status(12345,'/mcp','session-id','2025-06-18')
        self.assertEqual(status,f'HTTP_{code}')
        self.assertEqual(con.request.call_args.args,('DELETE','/mcp'))
        self.assertNotIn('X-Bridge-Local-Key',con.request.call_args.kwargs['headers'])
        self.assertEqual(con.request.call_args.kwargs['headers']['Mcp-Session-Id'],'session-id')
    def test_supported_delete_statuses(self):
        for code in (200,202,204,404,405):
            with self.subTest(code=code):self.check_status(code)
    def test_delete_unexpected_status_preserved(self):
        for code in (400,422,500,502,503):
            with self.subTest(code=code):self.check_status(code)
    def test_delete_network_timeout_preserved(self):
        con=Mock()
        con.getresponse.side_effect=TimeoutError()
        with patch.object(s.http.client,'HTTPConnection',return_value=con):
            self.assertEqual(s.cleanup_status(12345,'/mcp','s','v'),
                             'NETWORK_TimeoutError')
    def test_delete_disconnect_preserved(self):
        con=Mock()
        con.getresponse.side_effect=http.client.RemoteDisconnected('closed')
        with patch.object(s.http.client,'HTTPConnection',return_value=con):
            self.assertEqual(s.cleanup_status(12345,'/mcp','s','v'),
                             'NETWORK_RemoteDisconnected')

class Rollback(unittest.TestCase):
    def setUp(self):
        silent=patch.object(s,'mark')
        silent.start()
        self.addCleanup(silent.stop)
    def test_no_old_start_if_new_not_stopped(self):
        with patch.object(s,'graceful_stop',return_value=False),\
             patch.object(s,'wait_for',return_value=False),\
             patch.object(s,'active') as active:
            self.assertFalse(s.do_rollback('test',record=False))
            active.assert_not_called()
    def test_restore_old_after_new_stopped(self):
        with patch.object(s,'graceful_stop',side_effect=[True,True]) as stop,\
             patch.object(s,'wait_for',return_value=True),\
             patch.object(s,'active',return_value=True),\
             patch.object(s,'http_check',return_value=True),\
             patch.object(s,'plugin_unchanged'):
            self.assertTrue(s.do_rollback('test',record=False))
            self.assertEqual(stop.call_args_list[0].args,(s.NEW,))
            self.assertEqual(stop.call_args_list[1].args,(s.SHADOW,))
    def test_restore_old_when_inactive(self):
        with patch.object(s,'graceful_stop',side_effect=[True,True]),\
             patch.object(s,'wait_for',return_value=True),\
             patch.object(s,'active',return_value=False),\
             patch.object(s,'http_check',return_value=True),\
             patch.object(s,'cmd',return_value=Mock(returncode=0)) as cmd,\
             patch.object(s,'plugin_unchanged'):
            self.assertTrue(s.do_rollback('test',record=False))
            self.assertIn(('systemctl','--user','start',s.OLD),[x.args for x in cmd.call_args_list])
    def test_failed_old_restart_not_reported_as_restored(self):
        with patch.object(s,'graceful_stop',return_value=True),\
             patch.object(s,'wait_for',return_value=True),\
             patch.object(s,'active',return_value=False),\
             patch.object(s,'cmd',return_value=Mock(returncode=1)),\
             patch.object(s,'plugin_unchanged') as plugins:
            self.assertFalse(s.do_rollback('test',record=False))
            plugins.assert_not_called()

class CutoverGates(unittest.TestCase):
    def test_no_cutover_without_local_pass(self):
        with patch.object(s,'static_preflight'),\
             patch.object(s,'read_marker',return_value={'core_e2e_proven':False}),\
             patch.object(s,'private_marker') as create:
            with self.assertRaises(s.Stop):s.cutover_once()
            create.assert_not_called()
    def test_preflight_requires_old_active(self):
        with patch.object(s,'static_preflight'),\
             patch.object(s,'active',return_value=False):
            with self.assertRaises(s.Stop):s.preflight()
    def test_no_repeated_install(self):
        source=(Path(__file__).parent/'180_install_verify_and_cutover_once.sh').read_text()
        self.assertIn('TARGET_EXISTS_DO_NOT_REINSTALL',source)
    def test_no_write_tool_calls(self):
        source=Path(s.__file__).read_text()
        self.assertNotIn("'tools/call',{'name':'managed_create'",source)
        self.assertNotIn("'tools/call',{'name':'managed_set_dispatch'",source)
        self.assertNotIn("'tools/call',{'name':'managed_send_task'",source)
    def test_only_read_health_and_list(self):
        source=Path(s.__file__).read_text()
        self.assertIn("('managed_health',{},183)",source)
        self.assertIn("('managed_list',{'offset':0,'limit':10},184)",source)
    def test_old_launch_not_overwritten(self):
        source=(Path(__file__).parent/'180_install_verify_and_cutover_once.sh').read_text()
        self.assertNotIn('OLD_SCRIPT',source)
    def test_manual_rollback_script_exists(self):
        source=(Path(__file__).parent/'182_rollback_if_needed.sh').read_text()
        self.assertIn('stage35jk.py rollback',source)
    def test_verify_script_exists(self):
        source=(Path(__file__).parent/'181_verify_only.sh').read_text()
        self.assertIn('stage35jk.py verify',source)


class CutoverOrder(unittest.TestCase):
    def setUp(self):
        silent=patch.object(s,'mark')
        silent.start()
        self.addCleanup(silent.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        for name in ('CUT_MARK','CUT_PASS','ROLL_MARK'):
            patcher=patch.object(s,name,base/(name+'.json'))
            patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(s,'NEW_UNIT',base/'unit')
        patcher.start();self.addCleanup(patcher.stop)

    def test_success_stops_old_before_new_starts(self):
        state={s.OLD:True,s.NEW:False,s.SHADOW:False}
        events=[]
        def systctl(*args,**kwargs):
            if args[0] in ('start','stop'):
                unit=args[1]
                events.append((args[0],unit))
                state[unit]=(args[0]=='start')
            return ''
        with patch.object(s,'static_preflight'),\
             patch.object(s,'read_marker',return_value={'core_e2e_proven':True}),\
             patch.object(s,'active',side_effect=lambda unit:state.get(unit,False)),\
             patch.object(s,'port_closed',return_value=True),\
             patch.object(s,'http_check',return_value=True),\
             patch.object(s,'plugin_unchanged'),\
             patch.object(s,'install_new_unit'),\
             patch.object(s,'verify_unit'),\
             patch.object(s,'wait_for',side_effect=lambda fn,*a,**kw:fn()),\
             patch.object(s,'user_systemctl',side_effect=systctl),\
             patch.object(s.time,'sleep'):
            s.cutover_once()
        self.assertEqual(events,[('start',s.SHADOW),('stop',s.OLD),('start',s.NEW)])
        self.assertTrue(s.CUT_PASS.exists())
        self.assertTrue(s.CUT_MARK.exists())
        self.assertTrue(s.read_marker(s.CUT_PASS)['remote_plugin_verification_pending'])

    def test_new_start_failure_attempts_rollback(self):
        state={s.OLD:True,s.NEW:False,s.SHADOW:False}
        events=[]
        def systctl(*args,**kwargs):
            if args[0] in ('start','stop'):
                unit=args[1]
                events.append((args[0],unit))
                if args==('start',s.NEW):raise s.Stop('SIMULATED_START_FAILURE')
                state[unit]=(args[0]=='start')
            return ''
        with patch.object(s,'static_preflight'),\
             patch.object(s,'read_marker',return_value={'core_e2e_proven':True}),\
             patch.object(s,'active',side_effect=lambda unit:state.get(unit,False)),\
             patch.object(s,'port_closed',return_value=True),\
             patch.object(s,'http_check',return_value=True),\
             patch.object(s,'plugin_unchanged'),\
             patch.object(s,'install_new_unit'),\
             patch.object(s,'verify_unit'),\
             patch.object(s,'wait_for',side_effect=lambda fn,*a,**kw:fn()),\
             patch.object(s,'user_systemctl',side_effect=systctl),\
             patch.object(s,'do_rollback',return_value=True) as rollback,\
             patch.object(s.time,'sleep'):
            with self.assertRaisesRegex(s.Stop,'SIMULATED_START_FAILURE'):
                s.cutover_once()
        self.assertEqual(events,[('start',s.SHADOW),('stop',s.OLD),('start',s.NEW)])
        rollback.assert_called_once_with('CUTOVER_FAILURE',record=True)
        self.assertTrue(s.CUT_MARK.exists())
        self.assertFalse(s.CUT_PASS.exists())

if __name__=='__main__':unittest.main()

"""Offline regression tests (no systemd calls, no production services)."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import stage35l as m

class Integrity(unittest.TestCase):
    def test_original_35jk_pins(self):
        self.assertEqual(m.SOURCE_SHA,'5644bfca0617de256de4fe31fbb04ab4543c0862c2c49e2f26e6f03f80875c0d')
        self.assertEqual(m.RUN_SHA,'9c41cf36448069031669fcdd502507e10b78541734ffea1e7ace4ee6c10e485a')
    def test_source_directory_exact(self):
        self.assertEqual(set(x.name for x in Path(__file__).parent.iterdir()),m.PACKAGE_FILES)
    def test_sensitive_patterns_absent(self):
        text=Path(m.__file__).read_text()
        self.assertNotIn('CONTROL_PLANE_API_KEY=',text)
        self.assertNotIn('MCP_EXTRA_HEADERS=',text)
        self.assertNotIn('chmod -R',text)
        self.assertNotIn('dispatch_armed\':True',text)
    def test_one_shot_distinct_from_original(self):
        self.assertEqual(m.ATTEMPT.name,'stage35l-resume-attempt.json')
        self.assertNotEqual(m.ATTEMPT.name,'stage35k-cutover-attempt.json')
    def test_no_systemd_enable(self):
        text=Path(m.__file__).read_text()
        self.assertNotIn("user_systemctl('enable'",text)
    def test_no_old_source_edits(self):
        text=Path(m.__file__).read_text()
        self.assertNotIn('OLD_SCRIPT.write_',text)
        self.assertNotIn('SOURCE.write_',text)

class Files(unittest.TestCase):
    def test_marker_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'attempt.json'
            m.private_marker(f,{'attempted':True})
            self.assertEqual(json.loads(f.read_text()),{'attempted':True})
            self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
            with self.assertRaises(FileExistsError):m.private_marker(f,{'new':True})
    def test_regular_file_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'source';f.write_bytes(b'abc');f.chmod(0o600)
            self.assertTrue(m.owned_regular(f,hashlib.sha256(b'abc').hexdigest()))
            self.assertFalse(m.owned_regular(f,'0'*64))
            f.chmod(0o644)
            self.assertFalse(m.owned_regular(f))
    def test_symlink_and_hardlink_forbidden(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'source';f.write_bytes(b'ok');f.chmod(0o600)
            link=Path(td)/'link';link.symlink_to(f)
            self.assertFalse(m.owned_regular(link))
            hard=Path(td)/'hard';os.link(f,hard)
            self.assertFalse(m.owned_regular(f))
    def test_read_marker_not_json_dict(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'mark';f.write_text('[]');f.chmod(0o600)
            with self.assertRaises(m.Stop):m.read_marker(f)
    def test_read_marker_unsafe(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'mark';f.write_text('{}');f.chmod(0o666)
            with self.assertRaises(m.Stop):m.read_marker(f)

class DirectoryRepair(unittest.TestCase):
    def setupdirs(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        home=Path(tmp.name);conf=home/'.config';conf.mkdir(mode=0o755)
        conf.chmod(0o755)
        parent=conf/'systemd';parent.mkdir(mode=0o775);parent.chmod(0o775)
        unit=parent/'user';unit.mkdir(mode=0o775);unit.chmod(0o775)
        return home,parent,unit
    def test_fix_775_to_700(self):
        home,parent,unit=self.setupdirs()
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit),patch.object(m,'mark'):
            self.assertEqual(m.secure_unitdir(),0o775)
            self.assertEqual(stat.S_IMODE(unit.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode),0o700)
    def test_idempotent_700(self):
        home,parent,unit=self.setupdirs();parent.chmod(0o700);unit.chmod(0o700)
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit),patch.object(m,'mark'):
            self.assertEqual(m.secure_unitdir(),0o700)
            self.assertEqual(m.dir_state(),0o700)
    def test_preserve_safe_parent_755(self):
        home,parent,unit=self.setupdirs();parent.chmod(0o755)
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit),patch.object(m,'mark'):
            m.secure_unitdir()
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode),0o755)
    def test_unexpected_child_mode_rejected(self):
        home,parent,unit=self.setupdirs();unit.chmod(0o777)
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit):
            with self.assertRaises(m.Stop):m.secure_unitdir()
    def test_symlink_child_rejected(self):
        home,parent,unit=self.setupdirs();unit.rmdir();unit.symlink_to(home)
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit):
            with self.assertRaises(m.Stop):m.dir_state()
    def test_symlink_parent_rejected(self):
        home,parent,unit=self.setupdirs();unit.rmdir();parent.rmdir()
        other=home/'other';other.mkdir(mode=0o700);parent.symlink_to(other)
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',parent/'user'):
            with self.assertRaises(OSError):m.dir_state()
    def test_nonowner_rejected(self):
        home,parent,unit=self.setupdirs()
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit),patch('os.geteuid',return_value=os.geteuid()+1):
            with self.assertRaises(m.Stop):m.dir_state()
    def test_unsafe_config_parent_rejected(self):
        home,parent,unit=self.setupdirs();(home/'.config').chmod(0o777)
        with patch.object(m,'HOME',home),patch.object(m,'UNITDIR',unit):
            with self.assertRaises(m.Stop):m.dir_state()

class StateRules(unittest.TestCase):
    def test_required_old_markers(self):
        s=Mock();s.DEV_PASS=Path('/x/dev');s.CUT_MARK=Path('/x/cut');s.ROLL_MARK=Path('/x/roll')
        s.CUT_PASS=MagicMock();s.CUT_PASS.exists.return_value=False;s.CUT_PASS.is_symlink.return_value=False
        s.OLD='old';s.NEW='new';s.BASE='base';s.PG='pg';s.OLD_SCRIPT=Path('/a/old')
        s.show.return_value='/a/old';s.active.return_value=True
        s.NEW_UNIT=MagicMock();s.NEW_UNIT.exists.return_value=False;s.NEW_UNIT.is_symlink.return_value=False
        s.port_closed.return_value=True;s.http_check.return_value=True
        s.load_35g.return_value=Mock(check_original=Mock(),check_dropin=Mock())
        with patch.object(m,'package_preflight'),patch.object(m,'dir_state',return_value=0o775),\
             patch.object(m.os,'chdir'),patch.object(m,'read_marker',side_effect=[{'core_e2e_proven':False},{'attempted':True},{'rolled_back':True}]),\
             patch.object(m,'ATTEMPT',MagicMock()),patch.object(m,'MODE_MARK',MagicMock()),patch.object(m,'ACTIVE_MARK',MagicMock()),patch.object(m,'ROLL_MARK',MagicMock()):
            with self.assertRaises(m.Stop):m.base_preflight(s)
    def test_no_resume_when_marker_exists(self):
        source=Path(m.__file__).read_text()
        self.assertIn("'35L_ALREADY_ATTEMPTED'",source)
    def test_requires_original_rollback(self):
        source=Path(m.__file__).read_text()
        self.assertIn('PREVIOUS_ROLLBACK_MARKER_INVALID',source)
        self.assertIn("'PREVIOUS_E2E_NOT_PROVEN'",source)
    def test_501_not_a_cutover_gate(self):
        src=Path(m.__file__).read_text()
        self.assertNotIn('DELETE_501_FAILED',src)
        self.assertIn('core_e2e_proven',src)

class RollbackUnit(unittest.TestCase):
    def test_missing_new_unit_tolerated(self):
        s=Mock();s.NEW='new';s.active.return_value=False
        s.port_closed.return_value=True;s.wait_for.side_effect=lambda fun,t:fun()
        self.assertTrue(m.stop_new_if_present(s))
        s.cmd.assert_not_called()
    def test_failed_stop_but_inactive_tolerated(self):
        s=Mock();s.NEW='new';s.active.side_effect=[True,False]
        s.cmd.return_value=Mock(returncode=1);s.port_closed.return_value=True
        s.wait_for.side_effect=lambda fun,t:fun()
        with patch.object(m,'mark'):
            self.assertTrue(m.stop_new_if_present(s))
    def test_active_new_port_prevents_old_restart(self):
        s=Mock();s.NEW='new';s.active.return_value=True
        s.cmd.return_value=Mock(returncode=1);s.port_closed.return_value=False
        s.wait_for.return_value=False
        with patch.object(m,'mark'):
            self.assertFalse(m.rollback(s,'TEST'))
        self.assertEqual(s.cmd.call_count,1)
        self.assertNotIn('start',[c.args[2] for c in s.cmd.call_args_list])
    def test_old_already_active_no_relaunch(self):
        s=Mock();s.NEW='new';s.OLD='old';s.SHADOW='shadow'
        s.active.side_effect=lambda u: u=='old'
        s.port_closed.return_value=True;s.http_check.return_value=True
        s.wait_for.side_effect=lambda fn,t:fn()
        s.cmd.return_value=Mock(returncode=0)
        with tempfile.TemporaryDirectory() as td,patch.object(m,'ROLL_MARK',Path(td)/'rollback'),patch.object(m,'mark'):
            self.assertTrue(m.rollback(s,'TEST'))
            self.assertEqual([c.args for c in s.cmd.call_args_list],[])
            self.assertTrue((Path(td)/'rollback').exists())
    def test_old_start_only_after_new_down(self):
        state={'new':False,'old':False,'shadow':True}
        s=Mock();s.NEW='new';s.OLD='old';s.SHADOW='shadow'
        s.active.side_effect=lambda u:state[u]
        s.port_closed.return_value=True;s.http_check.return_value=True
        s.wait_for.side_effect=lambda fn,t:fn()
        def execute(*args,**kw):
            self.assertFalse(state['new'] if args[2]=='start' and args[3]=='old' else False)
            if args[2]=='start':state[args[3]]=True
            elif args[2]=='stop':state[args[4]]=False
            return Mock(returncode=0)
        s.cmd.side_effect=execute
        with tempfile.TemporaryDirectory() as td,patch.object(m,'ROLL_MARK',Path(td)/'rollback'),patch.object(m,'mark'):
            self.assertTrue(m.rollback(s,'TEST'))
        self.assertTrue(state['old']);self.assertFalse(state['shadow'])
    def test_shadow_still_active_is_incomplete(self):
        s=Mock();s.NEW='new';s.OLD='old';s.SHADOW='shadow'
        s.active.side_effect=lambda u:u in ('old','shadow')
        s.port_closed.return_value=True;s.http_check.return_value=True
        s.wait_for.side_effect=lambda fn,t:fn() if s.active('old') else False
        s.cmd.return_value=Mock(returncode=1)
        with tempfile.TemporaryDirectory() as td,patch.object(m,'ROLL_MARK',Path(td)/'rollback'),patch.object(m,'mark'):
            self.assertFalse(m.rollback(s,'TEST'))
            self.assertFalse((Path(td)/'rollback').exists())

class IntegrationMock(unittest.TestCase):
    def build_state(self,tmp,fail_new=False):
        state={'old':True,'new':False,'shadow':False}
        s=Mock();s.OLD='old';s.NEW='new';s.SHADOW='shadow'
        s.NEW_UNIT=tmp/'new.service';s.NEW_UNIT.write_text('unit')
        s.active.side_effect=lambda unit:state[unit]
        s.port_closed.side_effect=lambda port:not state['new'] if port==18778 else (not state['old'] if port==18774 else not state['shadow'])
        s.http_check.return_value=True
        s.wait_for.side_effect=lambda fn,sec,**kw:fn()
        def ctl(action,unit,**kw):
            if fail_new and action=='start' and unit=='new':raise m.Stop('INJECT_NEW_START_FAILURE')
            if action=='start':state[unit]=True
            elif action=='stop':state[unit]=False
        s.user_systemctl.side_effect=ctl
        def cmd(*args,**kw):
            if args[2]=='start':state[args[3]]=True
            if args[2]=='stop':state[args[4]]=False
            return Mock(returncode=0)
        s.cmd.side_effect=cmd
        s.install_new_unit.return_value=None
        return s,state
    def common_patches(self,tmp,s):
        patches=[patch.object(m,'load_jk',return_value=s),
            patch.object(m,'base_preflight'),patch.object(m,'secure_unitdir',return_value=0o775),
            patch.object(m,'dir_state',return_value=0o700),
            patch.object(m,'ATTEMPT',tmp/'attempt'),patch.object(m,'MODE_MARK',tmp/'secured'),
            patch.object(m,'ACTIVE_MARK',tmp/'active'),patch.object(m,'ROLL_MARK',tmp/'rolled'),
            patch.object(m.time,'sleep')]
        return patches
    def test_cutover_success(self):
        with tempfile.TemporaryDirectory() as td:
            tmp=Path(td);s,state=self.build_state(tmp)
            with __import__('contextlib').ExitStack() as stack:
                for p in self.common_patches(tmp,s):stack.enter_context(p)
                m.cutover_once()
                self.assertTrue((tmp/'active').exists())
                self.assertFalse((tmp/'rolled').exists())
            self.assertFalse(state['old']);self.assertTrue(state['new']);self.assertTrue(state['shadow'])
            self.assertEqual([x.args[:2] for x in s.user_systemctl.call_args_list],
                             [('start','shadow'),('stop','old'),('start','new')])
    def test_cutover_failure_automatically_restores_old(self):
        with tempfile.TemporaryDirectory() as td:
            tmp=Path(td);s,state=self.build_state(tmp,fail_new=True)
            with __import__('contextlib').ExitStack() as stack:
                for p in self.common_patches(tmp,s):stack.enter_context(p)
                with self.assertRaises(m.Stop):m.cutover_once()
                self.assertTrue((tmp/'attempt').exists())
                self.assertTrue((tmp/'rolled').exists())
                self.assertFalse((tmp/'active').exists())
            self.assertTrue(state['old']);self.assertFalse(state['new']);self.assertFalse(state['shadow'])

if __name__=='__main__':unittest.main()

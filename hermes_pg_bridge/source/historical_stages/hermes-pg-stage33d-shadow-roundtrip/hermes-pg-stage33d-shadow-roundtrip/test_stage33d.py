import pathlib
import unittest
from unittest.mock import MagicMock

import shadow33d as s
ROOT = pathlib.Path(__file__).resolve().parent


def cfg(path='/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs', **opts):
    return {'plugin':['/home/kali/.config/gigatool/plugins/mt-test-plugin.mjs',
                      [path,{'dry_run':False,'task_ids':[s.CANARY],**opts}]]}

class FakeActor:
    def __init__(self, role):
        self.role=role
        self.record={}
        self.meta={'last_seq':4,'next_actor':'chatgpt'}
        self.dialog=None
        self.delivery={}
        self.sent={}
    def health(self):return {'ok':True}
    def dialog_metadata(self,tid):
        if tid==s.CANARY:return self.meta
        return self.dialog
    def creation_status(self,key):
        return self.record.get(key)
    def delivery_status(self,tid,seq):
        if tid==s.CANARY:return {'status':'delivered'}
        return self.delivery.get(seq,{'status':'pending'})

class Stage33D(unittest.TestCase):
    def test_canary_config_absolute_path_and_uri(self):
        for path in ['/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs',
                     'file:///home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs']:
            s.verify_filter(cfg(path),plugin_src='const taskIds = Object.freeze(["'+s.CANARY+'"])')
    def test_reject_ungated_and_duplicate_plugin(self):
        for obj in [cfg(dry_run=True),cfg(task_ids=[]),
                    {'plugin':['/home/kali/bridge-pg-v2/dist/mt-pg-inbox.mjs']},
                    {'plugin':[cfg()['plugin'][1],cfg()['plugin'][1]]}]:
            with self.assertRaises(s.Stop):
                s.verify_filter(obj,plugin_src='Object.freeze("'+s.CANARY+'")')
    def test_reject_source_without_hardcoded_task(self):
        with self.assertRaises(s.Stop):s.verify_filter(cfg(),plugin_src='Object.freeze([])')
    def test_reject_alternate_uri_hosts_and_paths(self):
        for p in ['file://localhost/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs',
                  '/tmp/mt-pg-inbox-canary.mjs']:
            with self.assertRaises(s.Stop):s.verify_filter(cfg(p),plugin_src='Object.freeze("'+s.CANARY+'")')
    def test_readonly_preflight_does_not_issue_write(self):
        cg=FakeActor('chatgpt'); ag=FakeActor('agent')
        cg.create_dialog=MagicMock(side_effect=AssertionError('write attempted'))
        s.db_preflight(cg,ag)
        cg.create_dialog.assert_not_called()
    def test_refuse_existing_test_task(self):
        cg=FakeActor('chatgpt'); ag=FakeActor('agent')
        cg.record[s.REQUEST_ID]={'task_id':'task_'+'1'*32}
        with self.assertRaises(s.Stop):s.db_preflight(cg,ag)
    def test_fixed_task_and_report_no_dynamic_user_data(self):
        txt=(ROOT/'shadow33d.py').read_text()
        for v in ('STAGE33D_SQL_ROLE_TEST_ONLY_DO_NOT_EXECUTE',
                  'STAGE33D_SQL_ROLE_REPORT_OK_NO_MULTITOOL_SDK',
                  'cg.create_dialog(REQUEST_ID, TITLE)',
                  'ag.claim_message(tid, 1, TASK_MID, CONSUMER',
                  'cg.tombstone(tid, expected_version=2)'):
            self.assertIn(v,txt)
        self.assertNotIn('subprocess.run(',txt)
        self.assertNotIn('os.system(',txt)
        self.assertNotIn('bridge_app',txt)
    def test_scripts_dont_touch_services_and_use_shadow_port(self):
        for f in ('36_preflight.sh','36_run_once.sh','36_verify_only.sh'):
            txt=(ROOT/f).read_text()
            self.assertIn('APPDIR',txt)
            self.assertNotIn('systemctl --user restart',txt)
            self.assertNotIn('systemctl --user stop',txt)
            self.assertNotIn('15432',txt)
        self.assertIn('15433',(ROOT/'36_preflight.sh').read_text())
    def test_isolated_two_role_positive_cycle_and_cleanup(self):
        tid='task_'+'a'*32
        cg=MagicMock()
        ag=MagicMock()
        cg.create_dialog.return_value={'created':True,'task_id':tid,'initiator':'chatgpt'}
        cg.send_message.side_effect=[{'created':True,'seq':1,'version':1},
                                     {'created':False,'seq':1,'version':1}]
        ag.claim_message.return_value={'claimed':True,'lease_token':'token','delivery_id':15}
        ag.ack_outbox.return_value=True
        ag.delivery_status.return_value={'status':'delivered'}
        ag.send_report.return_value={'created':True,'seq':2,'version':2}
        cg.view_dialog.return_value={'messages':[
            {'seq':2,'sender':'agent','body':s.REPORT_BODY},
            {'seq':1,'sender':'chatgpt','body':s.TASK_BODY}]}
        cg.tombstone.return_value={'deleted':True}
        cg.dialog_metadata.side_effect=lambda k: {'state':'tombstoned','last_seq':2} if k==tid else {'last_seq':4}
        cg.delivery_status.return_value={'status':'cancelled'}
        s.perform(cg,ag)
        cg.send_message.assert_any_call(tid,s.TASK_MID,'task',s.TASK_BODY,expected_version=0)
        ag.send_report.assert_called_once_with(tid,s.REPORT_MID,'report',s.REPORT_BODY,expected_version=1)
        cg.tombstone.assert_called_once_with(tid,expected_version=2)

if __name__=='__main__':unittest.main()

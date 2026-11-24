from __future__ import annotations
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch
import cutover33fc as c


def fixture():
    entries=[]
    for i in range(8):
        entries.append([f'/home/kali/plugins/other{i}.mjs',{'dry_run':True}])
    entries[1]=['/home/kali/.config/gigatool/plugins/mt-git-inbox.mjs',{}]
    entries[5]=['/home/kali/bridge-pg-v2/dist/mt-pg-inbox-canary.mjs',
                {'dry_run':False,'task_ids':[c.CANARY]}]
    original={'plugin':entries,'other':{'theme':'light'}}
    draft=json.loads(json.dumps(original))
    draft['plugin'][5]=[str(c.CANDIDATE),{'dry_run':False,'task_ids':[c.CANARY]}]
    return json.dumps(original).encode(),json.dumps(draft).encode()

class Tests(unittest.TestCase):
    def test_exactly_seven_plugins_and_no_legacy(self):
        a,b=fixture();o=json.loads(c.make_final(a,b))
        self.assertEqual(len(o['plugin']),7)
        self.assertFalse(any('mt-git-inbox.mjs' in str(x) or 'mt-pg-inbox-canary.mjs' in str(x)
                             for x in o['plugin']))
        self.assertIn(str(c.CANDIDATE),[v[0] for v in o['plugin']])
    def test_unrelated_plugins_unchanged_in_order(self):
        a,b=fixture();a=json.loads(a);o=json.loads(c.make_final(json.dumps(a).encode(),b))
        kept=[x for x in a['plugin'] if 'mt-git-inbox.mjs' not in str(x) and 'mt-pg-inbox-canary.mjs' not in str(x)]
        for x in kept:self.assertIn(x,o['plugin'])
        self.assertEqual(o['other'],a['other'])
    def test_multiple_git_plugins_refused(self):
        a,b=fixture();o=json.loads(a);o['plugin'][3]=o['plugin'][1]
        with self.assertRaisesRegex(c.Stop,'plugin_targets_not_unique'):
            c.make_final(json.dumps(o).encode(),b)
    def test_missing_git_plugin_refused(self):
        a,b=fixture();o=json.loads(a);o['plugin'][1]=['/tmp/unrelated.mjs',{}]
        with self.assertRaisesRegex(c.Stop,'plugin_targets_not_unique'):
            c.make_final(json.dumps(o).encode(),b)
    def test_extra_config_edit_refused(self):
        a,b=fixture();o=json.loads(b);o['other']['theme']='dark'
        with self.assertRaisesRegex(c.Stop,'top_level_config_changed_in_draft'):
            c.make_final(a,json.dumps(o).encode())
    def test_unexpected_draft_plugin_edit_refused(self):
        a,b=fixture();o=json.loads(b);o['plugin'][2][1]['dry_run']=False
        with self.assertRaisesRegex(c.Stop,'unexpected_draft_edit'):
            c.make_final(a,json.dumps(o).encode())
    def test_no_git_or_canary_in_final(self):
        a,b=fixture();x=c.make_final(a,b).decode()
        self.assertNotIn('mt-git-inbox.mjs',x)
        self.assertNotIn('mt-pg-inbox-canary.mjs',x)
        self.assertIn('mt-pg-inbox-agent-stage33f.mjs',x)
    def test_worker_requires_quiet_period_and_no_arm(self):
        text=Path('cutover33fc.py').read_text()
        for exact in ('STABLE_SECONDS=12','WAIT_SECONDS=900',
                      "need(not (FB/'armed-stage33fc').exists()",
                      "need(not procs and not unknown,'runtime_changed_at_commit')"):
            self.assertIn(exact,text)
    def test_worker_does_not_kill_or_restart_mt(self):
        text=Path('cutover33fc.py').read_text()
        self.assertNotIn('os.kill(',text)
        self.assertNotIn("['pkill'",text)
        self.assertNotIn("['killall'",text)
        self.assertNotIn('systemctl --user restart',text)
    def test_tunnel_and_canary_not_activated(self):
        for p in Path('.').glob('*.sh'):
            text=p.read_text()
            self.assertNotIn('tunnel-client run',text)
            self.assertNotIn('stage33f_send_once',text)
            self.assertNotIn('touch armed-stage33fc',text)
        self.assertNotIn('send_message(',Path('cutover33fc.py').read_text())
    def test_handoff_unit_not_autoenabled(self):
        s=Path('40_install_handoff.sh').read_text()
        self.assertIn('Type=oneshot',s)
        self.assertIn('RemainAfterExit=yes',s)
        self.assertNotIn('WantedBy=',s)
        self.assertIn('systemctl --user daemon-reload',s)
    def test_manual_arm_required(self):
        s=Path('40_install_handoff.sh').read_text()
        self.assertNotIn('systemctl --user start --no-block',s)
        self.assertIn('systemctl --user start --no-block',Path('40_arm_handoff.sh').read_text())
    def test_arm_stops_only_legacy_git_services(self):
        s=Path('40_arm_handoff.sh').read_text()
        self.assertIn('hermes-bridge.service git-dialog-taskboard.service',s)
        self.assertNotIn('pkill',s)
        self.assertNotIn('multitool-deskt',s)
    def test_rollback_requires_no_mt_and_seq5_absent(self):
        src=Path('cutover33fc.py').read_text()
        beg=src.index('def rollback():');end=src.index('\ndef main(',beg)
        section=src[beg:end]
        self.assertIn('rollback_requires_all_mt_closed',section)
        self.assertIn('canary_unmodified_after_config_change()',section)
        self.assertIn('rollback_blocked_by_send_arm',section)
    def test_rollback_does_not_require_original_config_sha(self):
        src=Path('cutover33fc.py').read_text()
        beg=src.index('def rollback():');end=src.index('\ndef main(',beg)
        section=src[beg:end]
        self.assertNotIn('actor_guard()',section)
        self.assertIn('canary_unmodified_after_config_change()',section)

    def test_mode_is_private(self):
        src=Path('cutover33fc.py').read_text()
        self.assertIn('0o600',src)
        self.assertIn('os.fsync(',src)
        self.assertIn('os.replace(temp,path)',src)
    def test_no_user_commandline_leak(self):
        src=Path('cutover33fc.py').read_text()
        self.assertNotIn('decode(',src[src.index('def process_list'):src.index('def actor_guard')])
        self.assertIn("'process_count':len(procs)",src)
    def test_refuse_wrong_config_hash_in_prepare(self):
        src=Path('cutover33fc.py').read_text()
        self.assertIn("need(sha(original)==ORIG_SHA,'live_config_drift')",src)
    def test_shipped_scripts_syntax(self):
        import subprocess
        for p in Path('.').glob('*.sh'):
            q=subprocess.run(['bash','-n',str(p)],capture_output=True)
            self.assertEqual(q.returncode,0,str(p))

if __name__=='__main__':unittest.main()

import importlib.util,json,sys,unittest,tempfile,os,stat
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('stage36',Path(__file__).with_name('stage36.py'))
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
class Stage36Tests(unittest.TestCase):
    def test_01_pin_length(self):self.assertEqual(len(s.EXPECTED_CFG_SHA),64)
    def test_02_canary_distinct(self):self.assertNotEqual(s.CANARY,s.OLD_PILOT)
    def test_03_request_id_valid(self):self.assertLessEqual(len(s.REQUEST_ID),64)
    def test_04_permit_schema(self):
        self.assertEqual(set(s.permit_data(task_ids=[],allow_create=True,expiry=0)),{'version','expires_unix','config_sha256','task_ids','allow_create','static_tunnel_verified'})
    def test_05_create_empty_allowlist(self):
        p=s.permit_data(task_ids=[],allow_create=True,expiry=0)
        self.assertTrue(p['allow_create']);self.assertEqual(p['task_ids'],[])
    def test_06_scope_disables_create(self):
        p=s.permit_data(task_ids=['task_'+'a'*32],allow_create=False,expiry=0)
        self.assertFalse(p['allow_create']);self.assertEqual(len(p['task_ids']),1)
    def test_07_round_trip_json(self):
        o=s.permit_data(task_ids=[],allow_create=True,expiry=777)
        self.assertEqual(json.loads(s.encode(o)),o)
    def test_08_atom_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data';s.create_exclusive(p,b'hello')
            self.assertEqual(p.read_bytes(),b'hello')
            with self.assertRaises(FileExistsError):s.create_exclusive(p,b'evil')
    def test_09_file_private(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a';p.write_text('a');p.chmod(0o600)
            self.assertTrue(s.is_private_file(p))
            p.chmod(0o644);self.assertFalse(s.is_private_file(p))
    def test_10_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a';p.write_text('a');p.chmod(0o600)
            q=Path(d)/'b';q.symlink_to(p)
            self.assertFalse(s.is_private_file(q))
    def test_11_revoke_no_permit(self):
        with tempfile.TemporaryDirectory() as d,patch.object(s,'PERMIT',Path(d)/'none'),patch.object(s,'HOME',Path(os.environ.get('HOME', '/tmp'))),patch.object(s.pwd,'getpwuid',return_value=type('U',(),{'pw_name':'kali'})()),patch.object(s,'owner_safe_dir',return_value=True):
            s.revoke()
    def test_12_static_no_service_mutation(self):
        src=Path(__file__).with_name('stage36.py').read_text()
        self.assertNotIn("['systemctl','--user','restart'",src)
        self.assertNotIn('subprocess.Popen(',src)
    def test_13_no_db_mutations(self):
        src=Path(__file__).with_name('stage36.py').read_text()
        self.assertNotIn('INSERT INTO',src);self.assertNotIn('UPDATE bridge.',src)
    def test_14_bound_registry(self):
        src=Path(__file__).with_name('stage36.py').read_text()
        self.assertIn('len(listing)==2',src)
    def test_15_scope_requires_zero_messages(self):
        src=Path(__file__).with_name('stage36.py').read_text()
        self.assertIn("detail.get('last_seq')==0",src)
    def test_16_fail_closed_existing_permit(self):
        src=Path(__file__).with_name('stage36.py').read_text()
        self.assertIn('WRITE_PERMIT_ALREADY_PRESENT',src)
    def test_17_short_expiry(self):
        src=Path(__file__).with_name('stage36.py').read_text()
        self.assertIn('time.time())+1800',src);self.assertIn('time.time())+3600',src)
    def test_18_revoke_not_config_gated(self):
        import inspect
        self.assertNotIn('check_operator()',inspect.getsource(s.revoke))
    def test_19_readonly_preflight_command(self):
        self.assertIn("cmd=='preflight'",Path(__file__).with_name('stage36.py').read_text())
    def test_20_runtime_pin_length(self):self.assertEqual(len(s.EXPECTED_RUNTIME_SHA),64)
    def test_21_sha256(self):self.assertEqual(s.sha256(b'abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
if __name__=='__main__':unittest.main()

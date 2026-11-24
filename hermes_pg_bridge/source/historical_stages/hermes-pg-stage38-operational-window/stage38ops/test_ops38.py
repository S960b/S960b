import ast,hashlib,importlib.util,json,os,stat,tempfile,unittest
from pathlib import Path
P=Path(__file__).with_name('ops38.py')
spec=importlib.util.spec_from_file_location('stage38ops',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_parse(self):ast.parse(P.read_text())
 def test_constants(self):
  self.assertEqual(len(m.CFG_SHA),64)
  self.assertEqual(m.TASK,'task_b6c26aa18220412bafdbf32393f16568')
 def test_timing(self):self.assertLess(m.GATE_SEC_MAX,m.PERMIT_SEC)
 def test_paths(self):
  self.assertEqual(m.PERMIT.name,'managed-mcp-write-permit35c.json')
  self.assertEqual(m.GATE.name,'managed-writes-stage34d-armed')
 def test_scope_good(self):
  cfg={'plugin':['x'+str(i) for i in range(6)]+[str(m.CANARY),[str(m.CANDIDATE),{'dry_run':False,'tick_ms':30000,'task_ids':[m.TASK]}]]}
  m.check_scope(cfg)
 def test_reject_second_task(self):
  cfg={'plugin':['x'+str(i) for i in range(6)]+[str(m.CANARY),[str(m.CANDIDATE),{'dry_run':False,'tick_ms':30000,'task_ids':[m.TASK,'task_'+'a'*32]}]]}
  with self.assertRaises(m.Stop):m.check_scope(cfg)
 def test_reject_wrong_plugin(self):
  cfg={'plugin':['x'+str(i) for i in range(7)]+[[str(m.CANDIDATE),{'dry_run':False,'tick_ms':30000,'task_ids':[m.TASK]}]]}
  with self.assertRaises(m.Stop):m.check_scope(cfg)
 def test_reject_dryrun(self):
  cfg={'plugin':['x'+str(i) for i in range(6)]+[str(m.CANARY),[str(m.CANDIDATE),{'dry_run':True,'tick_ms':30000,'task_ids':[m.TASK]}]]}
  with self.assertRaises(m.Stop):m.check_scope(cfg)
 def test_exclusive_atomic_private(self):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'p';m.exclusive_private(f,b'data')
   self.assertEqual(f.read_bytes(),b'data')
   self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
   with self.assertRaises(FileExistsError):m.exclusive_private(f,b'other')
   self.assertEqual(f.read_bytes(),b'data')
 def test_no_file_symlink(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'p';p.symlink_to('/dev/null')
   with self.assertRaises(m.Stop):m.no_file(p)
 def test_no_file_existing(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'p';p.write_text('a')
   with self.assertRaises(m.Stop):m.no_file(p)
 def test_no_file_absent(self):
  with tempfile.TemporaryDirectory() as d:m.no_file(Path(d)/'none')
 def test_private_format(self):self.assertEqual(m.GATE_BYTES,b'ALLOW_MANAGED_WRITES_AFTER_EXCLUSIVE_SDK_REVIEW\n')
 def test_sha(self):self.assertEqual(m.hexdigest(b'x'),hashlib.sha256(b'x').hexdigest())
 def test_no_unbounded_task(self):self.assertNotIn('allow_create\':True',P.read_text())
 def test_no_live_db(self):
  s=P.read_text();self.assertNotIn('psycopg',s);self.assertNotIn('subprocess.run([\'psql\'',s)
 def test_no_timer_change(self):self.assertNotIn('systemctl\',\'--user\',\'enable',P.read_text())
if __name__=='__main__':unittest.main()

# Additional independently collected cases (unittest discovery picks these up).
class ArmTests(unittest.TestCase):
 def setUp(self):
  import contextlib
  from unittest.mock import patch
  self.temp=tempfile.TemporaryDirectory()
  self.d=Path(self.temp.name)
  self.patchers=[patch.object(m,'CAP',self.d),patch.object(m,'PERMIT',self.d/'permit'),patch.object(m,'GATE',self.d/'gate'),patch.object(m,'preflight',lambda:None),patch.object(m,'locked_against_watchdog',contextlib.nullcontext)]
  for p in self.patchers:p.start()
 def tearDown(self):
  for p in reversed(self.patchers):p.stop()
  self.temp.cleanup()
 def test_arm_creates_exact_scoped_files(self):
  import contextlib,io
  with contextlib.redirect_stdout(io.StringIO()) as out:m.arm()
  val=json.loads((self.d/'permit').read_text())
  self.assertEqual(val['task_ids'],[m.TASK])
  self.assertIs(val['allow_create'],False)
  self.assertEqual(val['config_sha256'],m.CFG_SHA)
  self.assertEqual((self.d/'gate').read_bytes(),m.GATE_BYTES)
  self.assertIn('STAGE38_NEXT_CHATGPT_ENABLE_DISPATCH_AND_SEND_ONE_TASK',out.getvalue())
 def test_arm_denies_if_existing_permit(self):
  (self.d/'permit').write_text('existing')
  with self.assertRaises(m.Stop):m.arm()
  self.assertEqual((self.d/'permit').read_text(),'existing')
  self.assertFalse((self.d/'gate').exists())
 def test_arm_denies_if_existing_gate(self):
  (self.d/'gate').write_text('existing')
  with self.assertRaises(m.Stop):m.arm()
  self.assertEqual((self.d/'gate').read_text(),'existing')
  self.assertFalse((self.d/'permit').exists())
 def test_abort_quarantines_gate_before_permit(self):
  import contextlib,io
  old=m.exclusive_private
  def broken(path,data):
   if path.name=='permit':raise OSError('test interrupted creating permit')
   old(path,data)
  from unittest.mock import patch
  with patch.object(m,'exclusive_private',broken):
   with contextlib.redirect_stdout(io.StringIO()):
    with self.assertRaises(OSError):m.arm()
  self.assertFalse((self.d/'gate').exists())
  self.assertFalse((self.d/'permit').exists())
  self.assertEqual(len(list(self.d.glob('.stage38-aborted-*'))),1)
 def test_abort_before_capability_created(self):
  from unittest.mock import patch
  with patch.object(m,'exclusive_private',side_effect=OSError('simulated disk failure')):
   with self.assertRaises(OSError):m.arm()
  self.assertFalse((self.d/'permit').exists())
  self.assertFalse((self.d/'gate').exists())

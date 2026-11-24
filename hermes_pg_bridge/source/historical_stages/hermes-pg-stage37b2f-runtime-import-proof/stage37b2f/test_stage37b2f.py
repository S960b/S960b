import hashlib, importlib.util, json, os, pathlib, shutil, subprocess, tempfile, unittest
PATH=pathlib.Path(__file__).with_name('stage37b2f.py')
spec=importlib.util.spec_from_file_location('stage37b2f',PATH)
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class TestInstrumentedCutover(unittest.TestCase):
  def candidate(self, default=False):
    base='export async function runTick(ctx) {return {status:"ok",taskIds:ctx.taskIds}}\nexport const markerName="live-handler";\n'
    return base + ('export default function plugin(){return "ok"}\n' if default else '')
  def config(self):
    return {'plugin':[[f'/private/plugin{i}.mjs',{}] for i in range(7)] + [[str(s.CANDIDATE),{'dry_run':False,'tick_ms':30000,'task_ids':[s.TASK]}]],'theme':'dark'}
  def test_candidate_export_validation(self):
    self.assertIn(b'export * from',s.wrapper_source(self.candidate(),'/tmp/proofs','d'*64))
  def test_candidate_default_detected(self):
    self.assertIn(b'export { default }',s.wrapper_source(self.candidate(True),'/tmp/proofs','d'*64))
  def test_candidate_named_only_no_default(self):
    self.assertNotIn(b'export { default }',s.wrapper_source(self.candidate(),'/tmp/proofs','d'*64))
  def test_reject_missing_runTick(self):
    with self.assertRaises(s.Stop):s.wrapper_source('export const a=1','/tmp/p','a'*64)
  def test_no_client_credentials_in_wrapper(self):
    w=s.wrapper_source(self.candidate(),'/tmp/proofs','a'*64)
    self.assertNotIn(b'Secret',w);self.assertNotIn(b'X-Bridge',w)
  def test_config_only_one_path(self):
    old=json.dumps(self.config()).encode();out=s.config_draft(old)
    a,b=json.loads(old),json.loads(out)
    self.assertEqual(a['plugin'][:7],b['plugin'][:7]);self.assertEqual(a['plugin'][7][1],b['plugin'][7][1]);self.assertEqual(b['plugin'][7][0],str(s.WRAPPER))
  def test_config_preserves_byte_values(self):
    old=json.dumps(self.config(),indent=2).encode();out=s.config_draft(old)
    self.assertEqual(out,old.replace(json.dumps(str(s.CANDIDATE)).encode(),json.dumps(str(s.WRAPPER)).encode(),1))
  def test_config_rejects_other_task(self):
    c=self.config();c['plugin'][7][1]['task_ids']=['task_'+'f'*32]
    with self.assertRaises(s.Stop):s.config_draft(json.dumps(c).encode())
  def test_config_rejects_extra_plugins(self):
    c=self.config();c['plugin'].append('oops')
    with self.assertRaises(s.Stop):s.config_draft(json.dumps(c).encode())
  def test_config_rejects_dry_run(self):
    c=self.config();c['plugin'][7][1]['dry_run']=True
    with self.assertRaises(s.Stop):s.config_draft(json.dumps(c).encode())
  def test_config_rejects_duplicate(self):
    c=self.config();c['plugin'][0][0]=str(s.CANDIDATE)
    with self.assertRaises(s.Stop):s.config_draft(json.dumps(c).encode())
  def test_config_rejects_non_last(self):
    c=self.config();c['plugin'][0],c['plugin'][7]=c['plugin'][7],c['plugin'][0]
    with self.assertRaises(s.Stop):s.config_draft(json.dumps(c).encode())
  def test_probe_reexports_and_proves_import(self):
    node=pathlib.Path('/home/kali/.hermes/node/bin/node')
    if not node.exists():node=shutil.which('node')
    if not node:self.skipTest('node not available')
    with tempfile.TemporaryDirectory() as td:
      d=pathlib.Path(td);proofs=d/'proofs';proofs.mkdir()
      target=d/'candidate.mjs';target.write_text(self.candidate(True))
      wrapper=d/'wrapper.mjs';wrapper.write_bytes(s.wrapper_source(target.read_text(),proofs,s.sha(target.read_bytes()),target))
      code=f'import * as m from {json.dumps(wrapper.as_uri())}; if (m.markerName!=="live-handler" || (await m.runTick({{taskIds:[1]}})).taskIds[0]!==1 || m.default()!=="ok") process.exit(2);'
      r=subprocess.run([str(node),'--input-type=module','-e',code],capture_output=True,timeout=15)
      self.assertEqual(r.returncode,0,r.stderr.decode())
      marks=list(proofs.glob('load-*.json'));self.assertEqual(len(marks),1)
      j=json.loads(marks[0].read_text());self.assertEqual(j['candidate_sha'],s.sha(target.read_bytes()));self.assertEqual(j['module_path'],str(target))
      self.assertEqual(marks[0].stat().st_mode&0o777,0o600)
  def test_import_probe_not_triggered_by_node_check(self):
    node=pathlib.Path('/home/kali/.hermes/node/bin/node')
    if not node.exists():node=shutil.which('node')
    if not node:self.skipTest('node not available')
    with tempfile.TemporaryDirectory() as td:
      d=pathlib.Path(td);p=d/'proofs';p.mkdir();target=d/'target.mjs';target.write_text(self.candidate())
      w=d/'wrapper.mjs';w.write_bytes(s.wrapper_source(target.read_text(),p,s.sha(target.read_bytes()),target))
      r=subprocess.run([str(node),'--check',str(w)],capture_output=True,timeout=15)
      self.assertEqual(r.returncode,0,r.stderr.decode());self.assertEqual(list(p.iterdir()),[])
  def test_node_probe_persistent_new_instance_creates_second(self):
    node=pathlib.Path('/home/kali/.hermes/node/bin/node')
    if not node.exists():node=shutil.which('node')
    if not node:self.skipTest('node not available')
    with tempfile.TemporaryDirectory() as td:
      d=pathlib.Path(td);p=d/'proofs';p.mkdir();target=d/'target.mjs';target.write_text(self.candidate())
      w=d/'wrapper.mjs';w.write_bytes(s.wrapper_source(target.read_text(),p,s.sha(target.read_bytes()),target))
      for _ in range(2):
        r=subprocess.run([str(node),'--input-type=module','-e',f'import {json.dumps(w.as_uri())}'],capture_output=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr.decode())
      self.assertEqual(len(list(p.glob('load-*.json'))),2)

if __name__=='__main__':unittest.main()

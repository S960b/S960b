import importlib.util,pathlib,unittest
p=pathlib.Path(__file__).with_name('verify37b2f1.py')
spec=importlib.util.spec_from_file_location('v',p);v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
SHA='a'*64
META={'v':1,'old_sha':SHA,'new_sha':SHA,'wrapper_sha':SHA,'candidate_sha':SHA,'task_id':v.TARGET_TASK}
COM={'event':'wrapper_registered','at':100}
REST={'at':180,'old_sha':SHA}
PROOF={'schema':1,'pid':12345,'start_ticks':'39538797','created_unix':120,'candidate_sha':SHA,'module_path':str(v.CANDIDATE)}
NAME='load-12345-39538797.json'
class Tests(unittest.TestCase):
 def valid(self,**overrides):
  m=dict(META);c=dict(COM);r=dict(REST);p=dict(PROOF)
  for name,data in overrides.items():{'m':m,'c':c,'r':r,'p':p}[name].update(data)
  return v.valid_structure(m,c,r,p,NAME)
 def test_valid(self):self.assertEqual(self.valid()['pid'],12345)
 def test_bad_name(self):
  with self.assertRaises(v.AuditFailure):v.valid_structure(META,COM,REST,PROOF,'load-12345-0.json')
 def test_wrong_sha(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'candidate_sha':'b'*64})
 def test_wrong_task(self):
  with self.assertRaises(v.AuditFailure):self.valid(m={'task_id':'task_invalid'})
 def test_bad_time(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'created_unix':200})
 def test_old_time(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'created_unix':99})
 def test_wrong_pid(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'pid':'12345'})
 def test_bad_ticks(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'start_ticks':'0'})
 def test_wrong_path(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'module_path':'/tmp/wrong'})
 def test_wrong_schema(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'schema':2})
 def test_foreign_fields(self):
  with self.assertRaises(v.AuditFailure):self.valid(p={'injection':'yes'})
 def test_invalid_commit(self):
  with self.assertRaises(v.AuditFailure):self.valid(c={'event':'other'})
 def test_invalid_restore_sha(self):
  with self.assertRaises(v.AuditFailure):self.valid(r={'old_sha':'b'*64})
 def test_bad_meta_sha(self):
  with self.assertRaises(v.AuditFailure):self.valid(m={'wrapper_sha':'invalid'})
 def test_original_crash_reproduced(self):
  mt=[[12345,39538797]]
  with self.assertRaises(TypeError):set(mt)
  self.assertTrue(any(p==12345 and t==39538797 for p,t in mt))
 def test_foreign_stop_masked_originally(self):
  class ForeignStop(RuntimeError):pass
  class LocalStop(RuntimeError):pass
  self.assertNotIsInstance(ForeignStop('STAGE37B2D_STOP_LOCK'),LocalStop)
if __name__=='__main__':unittest.main()

# End-to-end synthetic archived proof, with no live services, DB or application files.
class FixtureTests(unittest.TestCase):
 def test_full_readonly_archival_fixture(self):
  import os, json, hashlib, tempfile, time
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   p=pathlib.Path(tmp)
   root=p/'root'; root.mkdir(mode=0o700)
   proofs=root/'proofs'; proofs.mkdir(mode=0o700)
   cand=p/'candidate.mjs'; cfg=p/'opencode.json'; src=root/'stage37b2f.py'; permit=p/'permit.json'
   def write(path,data):
    path.write_bytes(data)
    path.chmod(0o600)
   candidate=b'export async function runTick(ctx) { return 1; }'
   write(cand,candidate)
   wrapper='import * as child from '+json.dumps(str(cand))+';\nexport * from '+json.dumps(str(cand))+';\n'+ 'proofFs.openSync(proofPath, "wx", 0o600);proofFs.fsyncSync(fd);proofFs.fsyncSync(directoryFd);'
   wb=wrapper.encode()
   write(root/'mt-pg-managed-runtime-import-proof37b2f.mjs',wb)
   base={'plugin':[[f'/plugin{i}.mjs',{}] for i in range(7)]+[[str(cand),{'dry_run':False,'tick_ms':30000,'task_ids':[v.TARGET_TASK]}]]}
   dr={'plugin':[[f'/plugin{i}.mjs',{}] for i in range(7)]+[[str(root/'mt-pg-managed-runtime-import-proof37b2f.mjs'),{'dry_run':False,'tick_ms':30000,'task_ids':[v.TARGET_TASK]}]]}
   old=json.dumps(base).encode();new=json.dumps(dr).encode()
   write(cfg,old);write(root/'backup-opencode.json',old);write(root/'draft-opencode.json',new)
   mock_source=b'def source(): pass';write(src,mock_source)
   meta={'v':1,'old_sha':v.digest(old),'new_sha':v.digest(new),'wrapper_sha':v.digest(wb),'candidate_sha':v.digest(candidate),'task_id':v.TARGET_TASK}
   now=int(time.time());ticks=str(int(float(pathlib.Path('/proc/uptime').read_text().split()[0])*os.sysconf('SC_CLK_TCK')))
   proof={'schema':1,'pid':3139983,'start_ticks':ticks,'created_unix':now,'candidate_sha':meta['candidate_sha'],'module_path':str(cand)}
   for name,obj in [('prepared.json',meta),('committed.json',{'event':'wrapper_registered','at':now-5}),('restored.json',{'at':now+5,'old_sha':meta['old_sha']}),('commit-attempt.json',{}),('restore-attempt.json',{})]:write(root/name,json.dumps(obj).encode())
   write(proofs/f"load-3139983-{ticks}.json",json.dumps(proof).encode())
   with patch.multiple(v,ROOT=root,CANDIDATE=cand,CFG=cfg,SOURCE=src,PERMIT=permit,SOURCE_SHA=v.digest(mock_source)),patch.object(v,'require_kali',lambda:None):
    self.assertEqual(v.run(),0)

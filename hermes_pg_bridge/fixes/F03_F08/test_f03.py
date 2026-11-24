#!/usr/bin/env python3
"""F03 negative tests against the actual patched CLI with private file fixtures."""
from __future__ import annotations
import importlib.util, json, os, pathlib, stat, sys, tempfile, time, unittest
from unittest import mock

class F03(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src=pathlib.Path(sys.argv[1]).resolve()
        sys.path.insert(0,str(src.parent))
        spec=importlib.util.spec_from_file_location("candidate34d",src)
        cls.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.m)
        cls.task="task_"+"a"*32
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        p=pathlib.Path(self.t.name);m=self.m
        self.paths={name:p/name for name in ["gate","permit","session","config","boot"]}
        for name,value in [("WRITE_ARM","gate"),("PERMIT","permit"),("SESSION","session"),("CFG","config"),("BOOT","boot")]:
            a=mock.patch.object(m,name,self.paths[value]);a.start();self.addCleanup(a.stop)
        self.now=int(time.time());self.task=self.__class__.task
        self.rawcfg=b'{"plugin":[]}\n'
        self.paths["config"].write_bytes(self.rawcfg);self.paths["config"].chmod(0o600)
        self.paths["boot"].write_text("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n")
        self.paths["gate"].write_bytes(m.ARM_CONTENT);self.paths["gate"].chmod(0o600)
        self.paths["permit"].chmod(0o600) if self.paths["permit"].exists() else None
        import hashlib
        sha=hashlib.sha256(self.rawcfg).hexdigest()
        self.session={"version":1,"session_id":"a"*32,"task_id":self.task,
          "config_sha256":sha,"boot_id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
          "created_unix":self.now-10,"deadline_unix":self.now+3600,
          "status":"active","reason":"","last_renew_unix":self.now-1}
        self.permit={"version":1,"expires_unix":self.now+1800,"config_sha256":sha,
          "task_ids":[self.task],"allow_create":False,"static_tunnel_verified":True}
        self.save()
    def save(self):
        self.paths["session"].write_text(json.dumps(self.session));self.paths["session"].chmod(0o600)
        self.paths["permit"].write_text(json.dumps(self.permit));self.paths["permit"].chmod(0o600)
    def test_valid_scoped(self):
        for op in ("claim","ack","append","authorize"):self.assertTrue(self.m.allow_write(op,self.task))
    def test_missing_expired_and_fault(self):
        self.permit["expires_unix"]=self.now-1;self.save();self.assertFalse(self.m.allow_write("claim",self.task))
        self.permit["expires_unix"]=self.now+1800;self.session["status"]="fault";self.save()
        self.assertFalse(self.m.allow_write("ack",self.task))
    def test_wrong_task_and_boot(self):
        self.assertFalse(self.m.allow_write("claim","task_"+"b"*32))
        self.paths["boot"].write_text("ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee\n")
        self.assertFalse(self.m.allow_write("append",self.task))
    def test_deadline_and_stale_keeper(self):
        self.session["deadline_unix"]=self.now-1;self.save();self.assertFalse(self.m.allow_write("authorize",self.task))
        self.session["deadline_unix"]=self.now+3600;self.session["last_renew_unix"]=self.now-300;self.save()
        self.assertFalse(self.m.allow_write("authorize",self.task))
    def test_config_drift_and_wrong_scope(self):
        self.paths["config"].write_bytes(b'{"plugin":["changed"]}')
        self.assertFalse(self.m.allow_write("claim",self.task))
        self.paths["config"].write_bytes(self.rawcfg)
        self.permit["task_ids"]=["task_"+"b"*32];self.save()
        self.assertFalse(self.m.allow_write("claim",self.task))
    def test_gate_absent_and_too_old(self):
        os.utime(self.paths["gate"],(self.now-1300,self.now-1300))
        self.assertFalse(self.m.allow_write("append",self.task))
        self.paths["gate"].unlink()
        self.assertFalse(self.m.allow_write("append",self.task))
    def test_injected_symlink_denied(self):
        self.paths["permit"].unlink()
        self.paths["permit"].symlink_to(self.paths["session"])
        self.assertFalse(self.m.allow_write("authorize",self.task))
    def test_no_global_gate(self):
        self.assertFalse(self.m.allow_write())
        self.assertFalse(self.m.allow_write("create",self.task))

if __name__=="__main__":
    unittest.main(argv=[sys.argv[0]],verbosity=1)

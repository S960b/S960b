import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import stage35n as s

class TestSecret(unittest.TestCase):
    def test_valid_with_newline(self):
        m,v=s.validate_secret(b'A'*64+b'\n')
        self.assertEqual(m,'newline');self.assertEqual(v,b'A'*64)
    def test_valid_without_newline(self):
        m,v=s.validate_secret(b'A'*64)
        self.assertEqual(m,'canonical');self.assertEqual(v,b'A'*64)
    def test_no_crlf(self):
        for v in (b'A'*64+b'\r\n',b'A'*63+b'\n',b'A'*65+b'\n',b'A'*64+b'\n\n',b'A'*64+b' ',b'A'*63+b'!\n'):
            with self.subTest(v=v[:5]):
                with self.assertRaises(s.StageStop):s.validate_secret(v)
    def test_noleak_marker(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'mark';s.journal(p,{'test':True})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):s.journal(p,{'test':True})
    def test_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_bytes(b'A'*64+b'\n');p.chmod(0o600)
            s.atomic_secret(p,b'A'*64)
            self.assertEqual(p.read_bytes(),b'A'*64)
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            s.atomic_secret(p,b'A'*64+b'\n')
            self.assertEqual(p.read_bytes(),b'A'*64+b'\n')
    def test_atomic_reject_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_bytes(b'A'*64);p.chmod(0o600)
            symlink=Path(d)/'link';symlink.symlink_to(p)
            with self.assertRaises(s.StageStop):s.atomic_secret(symlink,b'A'*64)
    def test_atomic_reject_group_read(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_bytes(b'A'*64);p.chmod(0o640)
            with self.assertRaises(s.StageStop):s.atomic_secret(p,b'A'*64)
    def test_restarts_once_success(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';raw=b'A'*64+b'\n';p.write_bytes(raw);p.chmod(0o600)
            calls=[];logs=[]
            def restart():calls.append(1)
            got=s.canonicalize_then_restart(p,raw,restart,lambda:True,logs.append)
            self.assertEqual(got,b'A'*64);self.assertEqual(p.read_bytes(),b'A'*64)
            self.assertEqual(len(calls),1)
            self.assertIn('KEY_CANONICALIZED_65_TO_64_BYTES_SECRET_NOT_PRINTED',logs)
    def test_rollback_if_probe_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';raw=b'A'*64+b'\n';p.write_bytes(raw);p.chmod(0o600)
            calls=[];logs=[]
            def restart():calls.append(1)
            status=iter([False,True])
            with self.assertRaises(s.StageStop):
                s.canonicalize_then_restart(p,raw,restart,lambda:next(status),logs.append)
            self.assertEqual(p.read_bytes(),raw)
            self.assertEqual(len(calls),2)
            self.assertIn('ROLLBACK_ORIGINAL_TUNNEL_RESTART_HEALTHY',logs)
    def test_rollback_if_restart_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';raw=b'A'*64+b'\n';p.write_bytes(raw);p.chmod(0o600)
            calls=[]
            def restart():
                calls.append(1)
                if len(calls)==1:raise RuntimeError('restart failed')
            with self.assertRaises(RuntimeError):
                s.canonicalize_then_restart(p,raw,restart,lambda:True,lambda x:None)
            self.assertEqual(p.read_bytes(),raw)
            self.assertEqual(len(calls),2)
    def test_no_mutation_if_already_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_bytes(b'A'*64);p.chmod(0o600)
            with self.assertRaises(s.StageStop):
                s.canonicalize_then_restart(p,b'A'*64,lambda:None,lambda:True,lambda x:None)
            self.assertEqual(p.read_bytes(),b'A'*64)

if __name__=='__main__':unittest.main()

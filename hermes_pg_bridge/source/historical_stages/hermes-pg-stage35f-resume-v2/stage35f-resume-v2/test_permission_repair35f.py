import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from permission_repair35f import verify_and_repair, open_pinned, Reject

class TestPinnedPermissionRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = self.root/'original.py'
        self.payload = b'print("pinned")\n'
        self.p.write_bytes(self.payload)
        self.pin = hashlib.sha256(self.payload).hexdigest()
        self.uid = os.getuid()
        self.p.chmod(0o644)
    def one(self, apply=False):
        return verify_and_repair([(self.p,self.pin)],self.uid,apply=apply)
    def test_validate_no_change(self):
        self.assertEqual(self.one(),[0o644])
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode),0o644)
    def test_apply_exactly_600(self):
        self.one(apply=True)
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode),0o600)
        self.assertEqual(self.p.read_bytes(),self.payload)
    def test_idempotent_600(self):
        self.one(apply=True)
        self.assertEqual(self.one(apply=True),[0o600])
    def test_changed_hash_no_chmod(self):
        self.p.write_bytes(b'changed')
        with self.assertRaisesRegex(Reject,'PINNED_CONTENT_CHANGED'):self.one(apply=True)
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode),0o644)
    def test_symlink_rejected(self):
        q = self.root/'other.py'
        self.p.rename(q)
        self.p.symlink_to(q)
        with self.assertRaises(OSError):self.one(apply=True)
        self.assertEqual(stat.S_IMODE(q.stat().st_mode),0o644)
    def test_hardlink_rejected(self):
        os.link(self.p,self.root/'other.py')
        with self.assertRaisesRegex(Reject,'LINKS'):self.one(apply=True)
    def test_unexpected_mode_rejected(self):
        self.p.chmod(0o666)
        with self.assertRaisesRegex(Reject,'UNEXPECTED_FILE_MODE'):self.one(apply=True)
    def test_exec_mode_rejected(self):
        self.p.chmod(0o755)
        with self.assertRaisesRegex(Reject,'UNEXPECTED_FILE_MODE'):self.one(apply=True)
    def test_group_writable_directory_rejected(self):
        d=self.root/'unsafe'; d.mkdir(mode=0o700)
        q=d/'source.py'; q.write_bytes(self.payload); q.chmod(0o644)
        d.chmod(0o770)
        with self.assertRaisesRegex(Reject,'DIR_OWNER_OR_PERMISSIONS_UNSAFE'):
            verify_and_repair([(q,self.pin)],self.uid,apply=True)
    def test_two_files_validate_before_mutation(self):
        second=self.root/'other.py'; second.write_bytes(b'x'); second.chmod(0o644)
        with self.assertRaisesRegex(Reject,'PINNED_CONTENT_CHANGED'):
            verify_and_repair([(self.p,self.pin),(second,self.pin)],self.uid,apply=True)
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode),0o644)
    def test_zero_length_rejected(self):
        self.p.write_bytes(b'')
        with self.assertRaisesRegex(Reject,'FILE_TYPE_OWNER_LINKS_SIZE_UNSAFE'):
            self.one(apply=True)
    def test_readme_no_write(self):
        self.one(apply=True)
        self.assertEqual(self.p.stat().st_size,len(self.payload))

if __name__=='__main__':unittest.main()

import ast, hashlib, pathlib, re, unittest
ROOT=pathlib.Path(__file__).parent
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.script=(ROOT/'stage36a_scope.py').read_text()
    def test_ast(self): ast.parse(self.script)
    def test_task_exact(self): self.assertIn('task_b6c26aa18220412bafdbf32393f16568', self.script)
    def test_title_exact(self): self.assertIn('Hermes PG v2 Independent E2E Stage 36 (dispatch disabled)', self.script)
    def test_old_title_pin(self): self.assertIn('PGV2 Stage36 Independent Managed E2E Canary',self.script)
    def test_request_id(self): self.assertIn('pgv2-stage36-independent-e2e-20261123-v1',self.script)
    def test_uses_existing_scope(self): self.assertIn('module.scope(TASK_ID)',self.script)
    def test_no_mcp_or_db_direct_calls(self):
        self.assertNotIn('psycopg',self.script)
        self.assertNotIn('subprocess',self.script)
        self.assertNotIn('os.system',self.script)
        self.assertNotIn('write_bytes',self.script)
    def test_hash_pin(self): self.assertRegex(self.script, r"EXPECTED_SHA = '[0-9a-f]{64}'")
if __name__ == '__main__':unittest.main()

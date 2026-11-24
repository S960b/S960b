import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from audit32 import SQL, validate_query

class Safety(unittest.TestCase):
    def test_read_queries_only(self):
        for name,q in SQL.items():
            validate_query(q)
    def test_mutations_rejected(self):
        for q in ['UPDATE bridge.messages SET body=1', 'SELECT 1; DELETE FROM bridge.messages',
                  'GRANT SELECT ON bridge.messages TO PUBLIC',
                  'SELECT 1 FROM t WHERE x=(SELECT 1);']:
            with self.assertRaises(RuntimeError): validate_query(q)
    def test_explicit_lease_readonly(self):
        self.assertIn('SET TRANSACTION READ ONLY',Path(__file__).with_name('audit32.py').read_text())
    def test_no_message_bodies(self):
        self.assertNotIn("SELECT body FROM bridge.messages",Path(__file__).with_name('audit32.py').read_text())

if __name__ == '__main__':
    unittest.main(verbosity=2)

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


    def test_canary_uses_only_publicly_granted_functions(self):
        q = SQL['canary'].lower()
        self.assertIn('bridge.dialog_metadata(', q)
        self.assertIn('bridge.delivery_status(', q)
        self.assertNotIn('from bridge.messages', q)
        self.assertNotIn('from bridge.outbox', q)
        self.assertNotIn('bridge.view_dialog(', q)  # no message bodies even internally
        self.assertEqual(q.count('%s'), 4)
        validate_query(q)

    def test_canary_is_explicitly_bounded(self):
        q = SQL['canary'].lower().replace(' ', '')
        self.assertIn('generate_series(1,4)', q)
        source = Path(__file__).with_name('audit32.py').read_text()
        self.assertIn("result['canary']['last_seq'] != 4", source)
        self.assertIn("result['canary']['message_count'] = result['canary']['last_seq']", source)

    def test_no_direct_table_reads_in_sql_queries(self):
        import re
        for name, q in SQL.items():
            self.assertIsNone(re.search(r'\bfrom\s+bridge\.(?:messages|outbox|dialogs)\b', q, re.I), name)
            self.assertIsNone(re.search(r'\bjoin\s+bridge\.(?:messages|outbox|dialogs)\b', q, re.I), name)

    def test_previously_failing_canary_no_longer_references_tables(self):
        import re
        q = SQL['canary']
        self.assertIsNone(re.search(r'(?i)\bbridge\.(messages|outbox)\b', q))


if __name__ == '__main__':
    unittest.main(verbosity=2)

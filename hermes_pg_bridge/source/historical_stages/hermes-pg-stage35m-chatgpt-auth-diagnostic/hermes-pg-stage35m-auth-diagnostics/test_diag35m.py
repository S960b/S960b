import unittest, importlib.util
from pathlib import Path
p=Path(__file__).with_name('diag35m.py')
s=importlib.util.spec_from_file_location('diag35m',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class T(unittest.TestCase):
 def test_whitelist(self):
  x={'secret_key':'sensitive','url':'x','state':'ready','poll_success_count':3,'nested':{'last_poll_at':'2026-11-23','connected':True}}
  y=m.extract_safe(x)
  self.assertIn(('state','ready'),y)
  self.assertIn(('poll_success_count','3'),y)
  self.assertIn(('nested.connected','true'),y)
  self.assertNotIn('sensitive',str(y))
 def test_metric_sanitization(self):
  x=b'# TYPE test gauge\ncommands_poll_last_successful_timestamp_seconds 1784900000\nmcp_requests_total{user="alice"} 1\nrequest_count 2\n'
  y=m.metric_summaries(x)
  self.assertIn(('commands_poll_last_successful_timestamp_seconds','nonzero'),y)
  self.assertIn(('request_count','2'),y)
  self.assertNotIn('alice',str(y))
 def test_port_flag(self): self.assertIsInstance(m.port_listening(65534),bool)
 def test_forbid_secret_output(self):
  self.assertIsNone(m.permitted_scalar('123456789012345678901234567890123456'))
  self.assertTrue(m.SECRET_RE.search('Authorization'))
if __name__=='__main__': unittest.main()

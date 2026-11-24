import unittest
from probe_logic import summarize_health, summarize_canary, CANARY_ID, CANARY_ACK

class TestReadonly(unittest.TestCase):
    def test_health_minimized(self):
        self.assertEqual(summarize_health({'ok': True, 'password': 'fake'}),
                         {'pg_connected': True, 'backend':'postgresql', 'read_only':True})
        self.assertFalse(summarize_health({'ok':'true'})['pg_connected'])
    def test_canary_receipt(self):
        v={'total_messages':2,'history':[{'from':'chatgpt','kind':'task','body':'other'},
              {'from':'mt','kind':'report','body':CANARY_ACK,'secret':'no'}]}
        r=summarize_canary(v)
        self.assertTrue(r['mt_ack_exact'])
        self.assertEqual(r['task_id'],CANARY_ID)
        self.assertNotIn('history',r)
        self.assertNotIn('secret',r)
    def test_canary_not_guessed(self):
        self.assertFalse(summarize_canary({'total_messages':1,'history':[
            {'from':'chatgpt','kind':'report','body':CANARY_ACK}]})['mt_ack_exact'])
    def test_no_writes_from_probe(self):
        from pathlib import Path
        src=Path(__file__).with_name('probe_server.py').read_text()
        for bad in ('create_dialog(', 'send_message(', 'claim_outbox(', 'ack_outbox(',
                    'dialog_send(', 'dialog_create(', 'bridge_app', 'open(', 'eval('):
            self.assertNotIn(bad,src)

if __name__=='__main__': unittest.main(verbosity=1)

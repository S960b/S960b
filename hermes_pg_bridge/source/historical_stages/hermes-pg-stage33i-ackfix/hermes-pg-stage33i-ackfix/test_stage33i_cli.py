"""Unit tests of the ACTUAL patched actor CLI ACK with fake PostgreSQL agent."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from build33i import patch_cli, CANARY

SRC=Path(__file__).with_name('stage33f_cli_reference.py')
ns={'__name__':'stage33i_mock_cli'}
exec(compile(patch_cli(SRC.read_text()),'<generated stage33i actor cli>','exec'),ns)
fn=ns['dispatch'];Stop=ns['Stop'];CONSUMER=ns['CONSUMER']
TOKEN='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'

def args(delivery=42,consumer=CONSUMER,token=TOKEN):
    return SimpleNamespace(command='ack',delivery_id=delivery,
        lease_token=token,consumer=consumer,task_id=CANARY)

class Fake:
    def __init__(self,states=('leased',),answer=True):
        self.states=iter(states);self.current=None;self.calls=0;self.answer=answer
    def delivery_status(self,task_id,seq):
        assert task_id==CANARY and seq==5
        try:self.current=next(self.states)
        except StopIteration:pass
        return {'delivery_id':42,'status':self.current}
    def ack_outbox(self,delivery_id,token,consumer):
        assert delivery_id==42 and token==TOKEN and consumer==CONSUMER
        self.calls+=1
        if self.answer=='raise':raise RuntimeError('network lost after commit')
        return self.answer

class Tests(unittest.TestCase):
    def test_lease_ack_success(self):
        f=Fake(('leased',),True)
        self.assertEqual(fn(args(),f),{'acked':True,'already_delivered':False})
        self.assertEqual(f.calls,1)
    def test_already_delivered_is_idempotent(self):
        f=Fake(('delivered',),False)
        self.assertEqual(fn(args(),f),{'acked':True,'already_delivered':True})
        self.assertEqual(f.calls,0)
    def test_lost_ack_response_db_delivered(self):
        f=Fake(('leased','delivered'),'raise')
        self.assertEqual(fn(args(),f),{'acked':True,'already_delivered':True})
        self.assertEqual(f.calls,1)
    def test_false_ack_response_db_delivered(self):
        f=Fake(('leased','delivered'),False)
        self.assertEqual(fn(args(),f),{'acked':True,'already_delivered':True})
    def test_unconfirmed_must_hold(self):
        f=Fake(('leased','leased'),False)
        with self.assertRaises(Stop):fn(args(),f)
        self.assertEqual(f.calls,1)
    def test_wrong_delivery_id_never_ack(self):
        f=Fake(('leased',),True)
        with self.assertRaises(Stop):fn(args(delivery=41),f)
        self.assertEqual(f.calls,0)
    def test_wrong_consumer_never_ack(self):
        f=Fake(('leased',),True)
        with self.assertRaises(Stop):fn(args(consumer='other'),f)
        self.assertEqual(f.calls,0)
    def test_pending_never_ack(self):
        f=Fake(('pending',),True)
        with self.assertRaises(Stop):fn(args(),f)
        self.assertEqual(f.calls,0)
    def test_invalid_token_never_ack(self):
        f=Fake(('leased',),True)
        with self.assertRaises(Stop):fn(args(token='invalid'),f)
        self.assertEqual(f.calls,0)

if __name__=='__main__':unittest.main()

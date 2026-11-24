"""Network-free tests for worker's contract and handling of retries."""
import pytest
pytest.importorskip('psycopg')
from bridge_pg.worker import Consumer


class FakeStore:
    def __init__(self):
        self.claims = [dict(delivery_id=7, task_id='task_'+'a'*32, seq=1,
                            message_id='m', sender='chatgpt', kind='task',
                            body='private task body', attempts=1,
                            lease_token='00000000-0000-0000-0000-000000000001')]
        self.acks = []
        self.releases = []
    def claim_outbox(self, *_args):
        rows, self.claims = self.claims, []
        return rows
    def ack_outbox(self, *a):
        self.acks.append(a)
        return True
    def release_outbox(self, *a, **kw):
        self.releases.append((a, kw))
        return True


class Inbox:
    def __init__(self, fail=False):
        self.fail = fail
        self.keys = set()
    def put_once(self, delivery):
        if self.fail: raise RuntimeError('private secret should not be logged')
        self.keys.add(delivery.dedup_key)


def test_success():
    s=FakeStore(); i=Inbox()
    assert Consumer(s,i,'agent','mt-v2').poll_once() == (1,0)
    assert len(s.acks)==1 and not s.releases
    assert i.keys == {'task_'+'a'*32+':1'}


def test_failure_release():
    s=FakeStore(); i=Inbox(fail=True)
    assert Consumer(s,i,'agent','mt-v2').poll_once() == (0,1)
    assert len(s.releases)==1 and not s.acks
    assert s.releases[0][1]['error']=='RuntimeError'

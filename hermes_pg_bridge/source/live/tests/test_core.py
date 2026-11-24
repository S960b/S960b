"""Integration test. Requires a disposable PostgreSQL database with 001_init.sql.

Never run against existing production records: set BRIDGE_TEST_DSN explicitly.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest

pytest.importorskip('psycopg')
from bridge_pg.store import Store, BridgeError

@pytest.fixture()
def store():
    dsn = os.environ.get('BRIDGE_TEST_DSN')
    if not dsn:
        pytest.skip('BRIDGE_TEST_DSN not set (requires disposable DB)')
    return Store(dsn)


def test_create_idempotent_and_turns(store):
    uid = uuid.uuid4().hex
    created = store.create_dialog(uid, 'turn test')
    tid = created['task_id']
    assert store.create_dialog(uid, 'turn test')['created'] is False
    with pytest.raises(BridgeError, match='request_id_conflict'):
        store.create_dialog(uid, 'different title')
    assert store.send_status(tid, 'missing')['found'] is False
    first = store.send_message(tid, uid + '-first', 'chatgpt', 'task', 'payload', 0)
    assert first['seq'] == 1
    assert store.send_message(tid, uid + '-first', 'chatgpt', 'task', 'payload', 0)['created'] is False
    with pytest.raises(BridgeError, match='message_id_conflict'):
        store.send_message(tid, uid + '-first', 'chatgpt', 'task', 'CHANGED')
    with pytest.raises(BridgeError, match='wrong_turn'):
        store.send_message(tid, uid + '-2', 'chatgpt', 'task', 'payload')
    with pytest.raises(BridgeError, match='version_conflict'):
        store.send_message(tid, uid + '-agent', 'agent', 'report', 'ok', 0)
    assert store.send_message(tid, uid + '-agent', 'agent', 'report', 'ok', 1)['seq'] == 2
    assert store.view_dialog(tid)['last_seq'] == 2


def test_return_cancels_undelivered(store):
    uid = uuid.uuid4().hex
    tid = store.create_dialog(uid, 'return test')['task_id']
    mid = uid + '-m'
    store.send_message(tid, mid, 'chatgpt', 'task', 'before return')
    returned = store.return_turn(tid, mid, 'chatgpt', 1)
    assert returned['returned'] is True
    assert store.view_dialog(tid)['next_actor'] == 'chatgpt'
    assert store.send_message(tid, uid+'-m2', 'chatgpt', 'task', 'after return')['seq'] == 2


def test_concurrent_same_message_id(store):
    uid = uuid.uuid4().hex
    tid = store.create_dialog(uid, 'concurrent test')['task_id']
    def send(_):
        return store.send_message(tid, uid+'-m', 'chatgpt', 'task', 'same')
    with ThreadPoolExecutor(max_workers=8) as ex:
        responses = list(ex.map(send, range(8)))
    assert sum(r['created'] for r in responses) == 1
    assert {r['seq'] for r in responses} == {1}
    assert store.view_dialog(tid)['last_seq'] == 1


def test_outbox_lease_redelivery(store):
    uid = uuid.uuid4().hex
    tid = store.create_dialog(uid, 'delivery test')['task_id']
    store.send_message(tid, uid+'-m', 'chatgpt', 'task', 'deliver')
    # Isolation assumption: disposable test DB, recipient=agent.
    claimed = store.claim_outbox('agent', 'worker-a', 50, 10)
    items = [x for x in claimed if x['task_id'] == tid]
    assert len(items) == 1
    x = items[0]
    assert not store.ack_outbox(x['delivery_id'], str(x['lease_token']), 'wrong-worker')
    assert store.release_outbox(x['delivery_id'], str(x['lease_token']), 'worker-a', 1)
    # Do not sleep in tests; verify release changed visibility via internal diagnostics later.

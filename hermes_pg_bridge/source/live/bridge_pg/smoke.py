"""Non-destructive (except creating test records) smoke test of a FRESH v2 DB.

Execute as bridge_app using libpq PGPASSFILE, never as postgres/root DB user.
The intentionally created test dialogue remains as an audit trail.
"""
from __future__ import annotations
import uuid
import sys
import psycopg
from .store import Store, BridgeError


def main() -> int:
    db = Store()
    run = uuid.uuid4().hex
    d = db.create_dialog('smoke-' + run, 'smoke postgres bridge', 'chatgpt')
    assert d['created'] and d['version'] == 0
    same = db.create_dialog('smoke-' + run, 'smoke postgres bridge', 'chatgpt')
    assert not same['created'] and same['task_id'] == d['task_id']
    tid = d['task_id']
    message_id = 'smoke-chatgpt-' + run
    sent = db.send_message(tid, message_id, 'chatgpt', 'task', 'v2 smoke message', 0)
    assert sent['created'] and sent['seq'] == 1
    retried = db.send_message(tid, message_id, 'chatgpt', 'task', 'v2 smoke message', 0)
    assert not retried['created'] and retried['seq'] == 1
    assert db.send_status(tid, message_id)['found']
    try:
        db.send_message(tid, 'other-' + run, 'chatgpt', 'task', 'wrong turn')
    except BridgeError as exc:
        assert exc.code == 'wrong_turn', exc.code
    else:
        raise AssertionError('turn checking failed')
    claimed = db.claim_outbox('agent', 'smoke-worker', 10, 60)
    mine = [x for x in claimed if x['task_id'] == tid]
    assert len(mine) == 1, 'smoke queue delivery not found (run on clean test server)'
    item = mine[0]
    assert db.ack_outbox(item['delivery_id'], str(item['lease_token']), 'smoke-worker')
    assert not db.ack_outbox(item['delivery_id'], str(item['lease_token']), 'smoke-worker')
    reply = db.send_message(tid, 'smoke-agent-' + run, 'agent', 'report', 'agent accepted', 1)
    assert reply['seq'] == 2
    view = db.view_dialog(tid)
    assert view['last_seq'] == 2 and len(view['messages']) == 2
    assert db.health()['ok']
    # The shared application login must not be able to SELECT data directly.
    with db._connect() as cn:
        try:
            with cn.cursor() as cur:
                cur.execute('SELECT count(*) FROM bridge.messages')
        except psycopg.errors.InsufficientPrivilege:
            cn.rollback()
        else:
            raise AssertionError('SECURITY FAILURE: bridge_app can read tables directly')
    print('SMOKE_OK', tid)
    return 0


if __name__ == '__main__':
    sys.exit(main())

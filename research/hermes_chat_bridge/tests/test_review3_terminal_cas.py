"""Terminal CAS is tested independently of the delivery-loop admission guard."""
import pytest
import bridge_server as bs
from test_review_3b082fe import bridge, stored_sub


@pytest.mark.parametrize('reason,status', [('delivered', 200), ('access_revoked', 410)])
def test_stale_failure_cannot_change_terminal_row_from_another_connection(bridge, reason, status):
    subscription = stored_sub(bridge)
    bridge.store.enqueue_delivery(subscription['id'], 'evt_terminal_cas', {})
    delivery = bridge.store.pending_deliveries()[0]
    second = bs.Store(bs.DB_PATH)
    try:
        # Simulate worker A finishing while worker B holds a stale work item.
        bridge.store.mark_delivery(delivery['id'], status, True, 0, reason)
        before = dict(bridge.store.conn.execute('SELECT * FROM deliveries WHERE id=?', (delivery['id'],)).fetchone())
        second.mark_delivery(delivery['id'], 503, False, 0, None)
        after = dict(bridge.store.conn.execute('SELECT * FROM deliveries WHERE id=?', (delivery['id'],)).fetchone())
        assert after == before, 'late retry changed terminal status, reason or attempt metadata'
    finally:
        second.conn.close()

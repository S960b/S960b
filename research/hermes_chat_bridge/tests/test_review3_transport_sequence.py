"""Non-vacuous TLS/prewrite cancellation check using actual delivery headers."""
import asyncio
import bridge_server as bs
from test_review_3b082fe import bridge, stored_sub


def test_tls_cancellation_prevents_request_bytes_and_closes_socket(bridge, monkeypatch):
    subscription = stored_sub(bridge)
    bridge.store.enqueue_delivery(subscription['id'], 'evt_tls_cancel', {'queue': 'test'})
    monkeypatch.setattr(bridge, '_validate_callback_url',
                        lambda url: ('callback.example', '93.184.216.34', 443))
    calls = []

    class Connection:
        def __init__(self, *args, **kwargs):
            self.connected = False

        def connect(self):
            calls.append('tls_connected')
            self.connected = True
            bridge.store.deactivate_sub(subscription['id'])

        def request(self, *args, **kwargs):
            # Faithful lazy-connect behavior of http.client; explicit connect
            # should avoid this branch and permit a post-TLS cancellation check.
            if not self.connected:
                self.connect()
            calls.append('request_bytes_written')

        def getresponse(self):
            return self

        status = 200

        def read(self, count):
            return b'{}'

        def close(self):
            calls.append('closed')

    monkeypatch.setattr(bs, 'PinnedHTTPSConnection', Connection)
    asyncio.run(bridge.deliver_pending())
    assert calls.count('tls_connected') == 1, 'test must reach TLS/setup, not fail early'
    assert 'request_bytes_written' not in calls, calls
    assert calls.count('closed') == 1, calls
    row = bridge.store.conn.execute('SELECT done FROM deliveries').fetchone()
    assert row['done'] == 1, 'cancelled request must not enter another retry'

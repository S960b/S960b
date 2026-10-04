import asyncio
import time
import pytest
from test_review_3b082fe import bridge, stored_sub


def test_expiry_during_dns_never_connects(bridge, monkeypatch):
    import bridge_server as bs
    s = stored_sub(bridge)
    def validate(url):
        s['expires_at']=time.time()-1
        bridge.store.save_sub(s)
        return 'callback.example', '93.184.216.34', 443
    monkeypatch.setattr(bridge, '_validate_callback_url', validate)
    monkeypatch.setattr(bs, 'PinnedHTTPSConnection', lambda *a: pytest.fail('expired subscription must not connect'))
    status, _ = asyncio.run(bridge._http_post(s['callback_url'],b'{}',{'webhook-id':'evt_x','X-MCP-Subscription-Id':s['id']}))
    assert status == 410

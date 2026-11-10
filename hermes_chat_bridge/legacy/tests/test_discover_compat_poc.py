"""ASGI/HTTP proof that the SDK blocker can be addressed narrowly.
Uses only temporary state and synthetic tokens, no external callbacks.
"""
import json
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_server as bs
from discover_events_compat_poc import install_discover_events_compat
from mcp.server.auth.provider import AccessToken

BASE = "https://bridge.example"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setattr(bs, 'OWNER_PASS', 'review-fixture-password')
    app = bs.build_app(BASE)
    b = app.state.bridge
    for name, resource in [("at_review", BASE + "/mcp"), ("at_other", "https://other.example/mcp")]:
        b.store.save_access(AccessToken(token=name, client_id="client_review", scopes=["bridge"],
            expires_at=int(time.time()) + 600, resource=resource, subject="owner"))
    with TestClient(app, base_url=BASE) as client:
        yield client
    b.queue._conn.close()
    b.store.conn.close()


def rpc(client, method, token="at_review"):
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method,
               "Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
        "method": method, "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}}}})


def test_discover_capabilities_survive_wire_serialization(api):
    r = rpc(api, "server/discover")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["result"]["capabilities"] == {"tools": {}, "events": {}}


def test_compat_does_not_allow_unauthenticated_discover(api):
    assert rpc(api, "server/discover", token=None).status_code == 401


def test_compat_does_not_allow_wrong_resource_token(api):
    assert rpc(api, "server/discover", token="at_other").status_code == 401


def test_tools_still_work(api):
    r = rpc(api, "tools/list")
    assert r.status_code == 200
    assert sorted(t["name"] for t in r.json()["result"]["tools"]) == [
        "bridge_get_message", "bridge_put_message", "bridge_put_reply",
        "bridge_subscribe", "bridge_subscription_status", "bridge_unsubscribe"]

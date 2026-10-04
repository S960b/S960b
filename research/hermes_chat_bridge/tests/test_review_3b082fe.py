"""Independent regression checks. Temporary databases, no external HTTP calls.

Run from research/hermes_chat_bridge with installed mcp==2.3.0, pytest, httpx:
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_review_3b082fe.py
Assertions express desired behavior; defects in 3b082fe intentionally fail.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_server as bs
from bridge_queue import Queue
from mcp.server.auth.provider import AccessToken

BASE = "https://bridge.example"
SECRET = "whsec_" + base64.b64encode(b"R" * 32).decode()


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "DB_PATH", str(tmp_path / "bridge.db"))
    monkeypatch.setattr(bs, "OWNER_PASS", "review-fixture-password")
    monkeypatch.setattr(bs, "OWNER_USER", "owner")
    b = bs.BridgeApp(BASE)
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    token = AccessToken(token='at_fixture', client_id='client_review', scopes=['bridge'], expires_at=int(time.time())+600, resource=BASE+'/mcp', subject='owner')
    b.store.save_access(token)
    context = auth_context_var.set(AuthenticatedUser(token))
    yield b
    auth_context_var.reset(context)
    b.queue._conn.close()
    b.store.conn.close()


def sub_params():
    return {"name": "hermes.message.created", "arguments": {"queue": "test"},
            "delivery": {"mode": "webhook", "url": "https://callback.example/hook",
                         "secret": SECRET}}


def stored_sub(b, *, expired=False):
    s = {"id": "sub_review", "owner": "owner", "event": "hermes.message.created",
         "arguments": {"queue": "test"}, "callback_url": "https://callback.example/hook",
         "secret": SECRET, "expires_at": time.time() + (-1 if expired else 3600), "active": True}
    b.store.save_sub(s)
    return s


def canonical_signature(secret, headers, body):
    # Independent verifier: Standard Webhooks covers ID, signing time, exact bytes.
    key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    signed = headers["webhook-id"].encode() + b"." + headers["webhook-timestamp"].encode() + b"." + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def test_verification_accepted_by_independent_standard_webhooks_verifier(bridge, monkeypatch):
    async def receiver(url, body, headers, timeout=10):
        expected = canonical_signature(SECRET, headers, body)
        if not hmac.compare_digest(headers["webhook-signature"], expected):
            return 401, b"{}"
        return 200, json.dumps({"challenge": json.loads(body)["challenge"]}).encode()
    monkeypatch.setattr(bridge, "_http_post", receiver)
    assert asyncio.run(bridge.verify_callback(stored_sub(bridge))) is True


def test_delivery_signed_with_id_and_exact_body(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s["id"], "evt_review", {"job_id": "job_fixture", "queue": "test"})
    calls = []
    async def receiver(url, body, headers, timeout=10):
        calls.append((body, headers))
        return 200, b"{}"
    monkeypatch.setattr(bridge, "_http_post", receiver)
    asyncio.run(bridge.deliver_pending())
    body, headers = calls[0]
    assert headers["webhook-signature"] == canonical_signature(SECRET, headers, body)


def test_failed_challenge_leaves_no_active_subscription(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_validate_callback_url", lambda url: None)
    async def fail(sub):
        return False
    monkeypatch.setattr(bridge, "verify_callback", fail)
    result = asyncio.run(bridge.events_subscribe(None, sub_params()))
    assert result["resultType"] == "error"
    assert bridge.store.active_subscriptions() == []


def test_failed_challenge_rechecked_on_repeat(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_validate_callback_url", lambda url: None)
    calls = []
    async def fail(sub):
        calls.append(sub["id"])
        return False
    monkeypatch.setattr(bridge, "verify_callback", fail)
    first = asyncio.run(bridge.events_subscribe(None, sub_params()))
    second = asyncio.run(bridge.events_subscribe(None, sub_params()))
    assert first["resultType"] == "error"
    assert second["resultType"] == "error"
    assert len(calls) == 2


def test_refresh_before_is_timezone_aware_iso_string(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_validate_callback_url", lambda url: None)
    async def ok(sub):
        return True
    monkeypatch.setattr(bridge, "verify_callback", ok)
    result = asyncio.run(bridge.events_subscribe(None, sub_params()))
    assert isinstance(result["refreshBefore"], str), result["refreshBefore"]
    assert datetime.fromisoformat(result["refreshBefore"].replace("Z", "+00:00")).tzinfo is not None


def test_no_delivery_after_subscription_expiry(bridge, monkeypatch):
    s = stored_sub(bridge, expired=True)
    bridge.store.enqueue_delivery(s["id"], "evt_review", {"job_id": "job_fixture", "queue": "test"})
    calls = []
    async def receiver(url, body, headers, timeout=10):
        calls.append(url)
        return 200, b"{}"
    monkeypatch.setattr(bridge, "_http_post", receiver)
    asyncio.run(bridge.deliver_pending())
    assert calls == []


def test_retries_eventually_become_terminal(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s["id"], "evt_review", {"job_id": "job_fixture", "queue": "test"})
    bridge.store.conn.execute("UPDATE deliveries SET attempts=1000")
    bridge.store.conn.commit()
    async def unavailable(url, body, headers, timeout=10):
        return 503, b"{}"
    monkeypatch.setattr(bridge, "_http_post", unavailable)
    asyncio.run(bridge.deliver_pending())
    row = bridge.store.conn.execute("SELECT * FROM deliveries").fetchone()
    assert row["done"] == 1, dict(row)


def test_body_and_occurrence_timestamp_stable_across_retry(bridge, monkeypatch):
    s = stored_sub(bridge)
    bridge.store.enqueue_delivery(s["id"], "evt_review", {"job_id": "job_fixture", "queue": "test"})
    clock = iter(["2026-10-04T11:00:00Z", "2026-10-04T11:00:10Z"])
    monkeypatch.setattr(bs, "utc_iso", lambda: next(clock))
    calls = []
    async def unavailable(url, body, headers, timeout=10):
        calls.append(body)
        return 503, b"{}"
    monkeypatch.setattr(bridge, "_http_post", unavailable)
    asyncio.run(bridge.deliver_pending())
    bridge.store.conn.execute("UPDATE deliveries SET next_attempt=0")
    bridge.store.conn.commit()
    asyncio.run(bridge.deliver_pending())
    assert calls[0] == calls[1]


def test_public_login_does_not_interpret_state_as_html():
    # Harmless marker, no JavaScript or credential capture.
    state = '\"><span id="review-sentinel">'
    response = bs.login_page(state, BASE)
    assert b'<span id="review-sentinel">' not in response.body


def test_old_login_state_rejected(bridge):
    state = "review-old-state"
    bridge.store.save_state(state, {
        "client_id": "client_review", "redirect_uri": "https://callback.example/oauth",
        "redirect_uri_provided_explicitly": True, "code_challenge": "R" * 43,
        "resource": BASE + "/mcp"})
    bridge.store.conn.execute("UPDATE state_map SET created_at=?", (time.time() - 86400,))
    bridge.store.conn.commit()
    from starlette.applications import Starlette
    from starlette.routing import Route
    app = Starlette(routes=[Route("/login/callback", bridge.login_callback, methods=["POST"])])
    with TestClient(app, base_url=BASE) as client:
        r = client.post("/login/callback", data={"username": "owner",
                        "password": "review-fixture-password", "state": state}, follow_redirects=False)
    assert r.status_code == 400, r.status_code


@pytest.fixture
def api(bridge, monkeypatch):
    app = bs.build_app(BASE)
    b = app.state.bridge
    b.store.save_access(AccessToken(token="at_review", client_id="client_review", scopes=["bridge"],
                                   expires_at=int(time.time()) + 600, resource=BASE + "/mcp", subject="owner"))
    with TestClient(app, base_url=BASE) as client:
        yield client, b
    b.queue._conn.close()
    b.store.conn.close()


def rpc(client, method, params=None, token="at_review"):
    p = dict(params or {})
    p["_meta"] = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                  "io.modelcontextprotocol/clientCapabilities": {}}
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method,
               "Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": p}, headers=headers)


def test_discover_events_on_actual_http_response(api):
    client, b = api
    response = rpc(client, "server/discover")
    assert response.status_code == 200, response.text
    assert "events" in response.json()["result"]["capabilities"], response.json()


def test_mcp_rejects_missing_token(api):
    client, b = api
    assert rpc(client, "tools/list", token=None).status_code == 401


def test_mcp_rejects_wrong_resource_token(api):
    client, b = api
    b.store.save_access(AccessToken(token="at_wrong_resource", client_id="client_review", scopes=["bridge"],
                                   expires_at=int(time.time()) + 600, resource="https://other.example/mcp", subject="owner"))
    assert rpc(client, "tools/list", token="at_wrong_resource").status_code == 401


def test_queue_happy_path_and_no_unknown_creation(bridge):
    job = bridge.queue.put_message("synthetic fixture")
    assert bridge.queue.put_reply(job["job_id"], "OK")["status"] == "replied"
    assert bridge.queue.put_reply(job["job_id"], "OK")["idempotent"] is True
    assert bridge.queue.put_reply(job["job_id"], "OTHER")["status"] == "conflict"
    assert bridge.queue.get_message(job["job_id"])["reply"] == "OK"
    assert bridge.queue.put_reply("job_unknown", "OK")["status"] == "not_found"
    assert bridge.queue.get_message("job_unknown") is None


def test_queue_utf8_byte_limit(bridge):
    with pytest.raises(ValueError):
        bridge.queue.put_message("я" * 4097)


def test_queue_concurrent_reply_cannot_silently_overwrite(tmp_path):
    path = str(tmp_path / "race.db")
    q1, q2 = Queue(path), Queue(path)
    job = q1.put_message("fixture")["job_id"]
    barrier = Barrier(2, timeout=3)

    class Cursor:
        def __init__(self, c):
            self.c = c
        def fetchone(self):
            row = self.c.fetchone()
            barrier.wait()
            return row

    class Connection:
        def __init__(self, c):
            self.c = c
        def execute(self, sql, *args):
            if sql.startswith("UPDATE jobs SET"):
                barrier.wait()
            cur = self.c.execute(sql, *args)
            if False:
                return Cursor(cur)
            return cur
        def commit(self):
            return self.c.commit()

    c1, c2 = q1._conn, q2._conn
    q1._conn, q2._conn = Connection(c1), Connection(c2)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(q1.put_reply, job, "FIRST")
            b = executor.submit(q2.put_reply, job, "SECOND")
            results = [a.result(timeout=5), b.result(timeout=5)]
        assert sorted(r["status"] for r in results) == ["conflict", "replied"], results
    finally:
        c1.close()
        c2.close()

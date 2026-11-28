import json

import pytest

from git_dialog import DialogError
from git_dialog.gateway import WriteGateway
from git_dialog.ingress import AccessAuthority, Dispatcher
from test_confirmation import setup as setup_confirmation, cleanup
from test_github import TASK, writes


ORIGIN = "http://127.0.0.1:18878"


def setup(tmp_path, mutations=True):
    api, registry, box, store, confirmation = setup_confirmation(tmp_path)
    authority = AccessAuthority({"configured_owner": {"role": "owner", "actor": None},
        "mt_worker": {"role": "agent", "actor": "mt"},
        "chatgpt_worker": {"role": "agent", "actor": "chatgpt"}})
    gateway = WriteGateway(confirmation.coordinator.dialog, registry, TASK, actors=("chatgpt", "mt"))
    dispatcher = Dispatcher(gateway, confirmation, authority, origin=ORIGIN, mutations=mutations)
    return api, registry, box, store, authority, dispatcher


def request(dispatcher, grant, obj, *, now=101, headers=None, body=None):
    headers = headers if headers is not None else [
        ("Host", "127.0.0.1:18878"), ("Origin", ORIGIN),
        ("Authorization", "Bearer " + grant["session"]), ("X-CSRF-Token", grant["csrf"]),
        ("Content-Type", "application/json")]
    body = json.dumps(obj).encode() if body is None else body
    return dispatcher.dispatch("POST", "/api/task", headers, body, now=now)


def test_owner_view_pending_cancel_and_confirm_prepare_only(tmp_path):
    api, registry, box, store, authority, d = setup(tmp_path)
    grant = authority.issue("configured_owner", now=100)
    assert request(d, grant, {"op": "view", "offset": 0, "limit": 20})["task_id"] == TASK
    intent = request(d, grant, {"op": "request_close", "head": api.initial})
    assert registry.get(TASK)["state"] == "active"
    request(d, grant, {"op": "cancel_close", "nonce": intent["confirmation"]})
    assert registry.get(TASK)["state"] == "active"
    intent = request(d, grant, {"op": "request_close", "head": api.initial})
    assert request(d, grant, {"op": "confirm_close", "nonce": intent["confirmation"]})[
        "status"] == "terminal_intent_prepared_not_published"
    with pytest.raises(DialogError, match="confirmation_denied"):
        request(d, grant, {"op": "confirm_close", "nonce": intent["confirmation"]})
    assert not writes(api) and not box.get(TASK)["attempted"]
    cleanup(registry, box, store)


def test_agent_actor_from_server_binding_and_repeated_append_deduplicated(tmp_path):
    api, registry, box, store, authority, d = setup(tmp_path)
    grant = authority.issue("chatgpt_worker", now=100)
    obj = {"op": "append", "head": api.initial, "message_id": "m1", "kind": "task", "body": "data"}
    first = request(d, grant, obj)
    assert first["created"]
    api.calls.clear()
    obj["head"] = first["commit"]
    assert not request(d, grant, obj)["created"] and not writes(api)
    cleanup(registry, box, store)


@pytest.mark.parametrize("op", ["request_close", "cancel_close", "confirm_close"])
def test_agent_cannot_invoke_owner_lifecycle(tmp_path, op):
    api, registry, box, store, authority, d = setup(tmp_path)
    grant = authority.issue("mt_worker", now=100)
    obj = {"op": op, **({"head": api.initial} if op == "request_close" else {"nonce": "data"})}
    with pytest.raises(DialogError, match="rpc_role_denied"): request(d, grant, obj)
    assert not api.calls
    cleanup(registry, box, store)


@pytest.mark.parametrize("field", ["actor", "principal", "role", "task_id", "force"])
def test_body_cannot_select_authority_or_task(tmp_path, field):
    api, registry, box, store, authority, d = setup(tmp_path)
    grant = authority.issue("mt_worker", now=100)
    with pytest.raises(DialogError, match="rpc_schema_denied"):
        request(d, grant, {"op": "view", "offset": 0, "limit": 20, field: "owner"})
    assert not api.calls
    cleanup(registry, box, store)


@pytest.mark.parametrize("case", ["host", "origin", "csrf", "session", "duplicate", "proxy", "framing"])
def test_untrusted_headers_rejected_before_task_access(tmp_path, case):
    api, registry, box, store, authority, d = setup(tmp_path)
    grant = authority.issue("configured_owner", now=100)
    headers = [("Host", "127.0.0.1:18878"), ("Origin", ORIGIN),
        ("Authorization", "Bearer " + grant["session"]), ("X-CSRF-Token", grant["csrf"]),
        ("Content-Type", "application/json")]
    if case == "host": headers[0] = ("Host", "attacker.example")
    if case == "origin": headers[1] = ("Origin", "http://evil.example")
    if case == "csrf": headers[3] = ("X-CSRF-Token", "a" * 43)
    if case == "session": headers[2] = ("Authorization", "Bearer " + "b" * 43)
    if case == "duplicate": headers.append(("hOsT", "127.0.0.1:18878"))
    if case == "proxy": headers.append(("X-Forwarded-Host", "localhost"))
    if case == "framing": headers.append(("Transfer-Encoding", "chunked"))
    with pytest.raises(DialogError):
        request(d, grant, {"op": "view", "offset": 0, "limit": 20}, headers=headers)
    assert not api.calls
    cleanup(registry, box, store)


@pytest.mark.parametrize("body", [b'{}', b'{"op":"view","op":"append"}', b'\xff',
    b'{"op":NaN}', b'x' * 8193, b'[' * 1500 + b']' * 1500])
def test_malformed_or_large_body_rejected(tmp_path, body):
    api, registry, box, store, authority, d = setup(tmp_path)
    grant = authority.issue("configured_owner", now=100)
    with pytest.raises(DialogError): request(d, grant, {}, body=body)
    assert not api.calls
    cleanup(registry, box, store)


def test_default_read_only_and_owner_cannot_impersonate_agent(tmp_path):
    api, registry, box, store, authority, d = setup(tmp_path, mutations=False)
    grant = authority.issue("configured_owner", now=100)
    with pytest.raises(DialogError, match="rpc_mutations_disabled"):
        request(d, grant, {"op": "request_close", "head": api.initial})
    d.mutations = True
    with pytest.raises(DialogError, match="rpc_role_denied"):
        request(d, grant, {"op": "append", "head": api.initial, "message_id": "bad", "kind": "task", "body": "data"})
    assert not api.calls
    cleanup(registry, box, store)


def test_expiry_revocation_and_restart_fail_closed():
    a = AccessAuthority({"owner": {"role": "owner", "actor": None}})
    g = a.issue("owner", now=100, ttl=10)
    assert a.resolve(g["session"], g["csrf"], now=109)["role"] == "owner"
    for clock in (99, 110):
        with pytest.raises(DialogError, match="access_denied"):
            a.resolve(g["session"], g["csrf"], now=clock)
    a.revoke(g["session"])
    with pytest.raises(DialogError): a.resolve(g["session"], g["csrf"], now=101)
    with pytest.raises(DialogError): AccessAuthority().resolve(g["session"], g["csrf"], now=101)
    with pytest.raises(DialogError): AccessAuthority().issue("owner", now=100)


@pytest.mark.parametrize("origin", ["http://evil.example", "http://localhost:0", "https://x/path", "https://a@x", "https://x#fragment"])
def test_invalid_configured_origins(tmp_path, origin):
    api, registry, box, store, authority, d = setup(tmp_path)
    with pytest.raises(DialogError, match="invalid_ingress_origin"):
        Dispatcher(d.gateway, d.confirmation, authority, origin=origin)
    cleanup(registry, box, store)


def test_csrf_from_different_session_rejected():
    a = AccessAuthority({"owner": {"role": "owner", "actor": None}})
    one, two = a.issue("owner", now=100), a.issue("owner", now=100)
    with pytest.raises(DialogError, match="access_denied"):
        a.resolve(one["session"], two["csrf"], now=101)


@pytest.mark.parametrize("method,path", [("GET", "/api/task"), ("POST", "/api/task?owner=true"),
                                        ("POST", "/login")])
def test_no_login_query_or_get_routes(tmp_path, method, path):
    api, registry, box, store, authority, d = setup(tmp_path)
    with pytest.raises(DialogError, match="rpc_route_denied"):
        d.dispatch(method, path, [], b"{}", now=101)
    assert not api.calls
    cleanup(registry, box, store)

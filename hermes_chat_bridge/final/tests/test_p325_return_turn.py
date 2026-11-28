"""P32.5 offline owner-return contract: fake Git and local UI, no network."""
import io
import json

import pytest

from git_dialog import DialogError, parse_snapshot
from git_dialog.authority import CommonAuthority
from git_dialog.taskboard import Taskboard
from git_dialog.unix_authority import Runtime
from test_dialog_catalog import catalog


@pytest.mark.parametrize("initiator", ["mt", "chatgpt"])
def test_only_last_message_returned_and_replaced(catalog, initiator):
    api, service = catalog
    created = service.create(initiator, "p325_" + initiator, "Return test")
    task = created["task_id"]
    first = service.append(initiator, task, head=created["head"],
                           message_id="first_v1", kind="task", body="old")
    returned = service.return_turn(task, first["commit"], "first_v1")
    assert returned["returned_message_id"] == "first_v1"
    assert returned["waiting_for"] == initiator
    assert returned["created"] is True
    view = service.view(task)
    assert view["waiting_for"] == initiator
    assert view["awaiting_replacement"] is True
    assert view["active_message_id"] is None
    assert [m["kind"] for m in view["history"][:2]] == ["return", "task"]
    assert view["history"][1]["returned"] is True
    assert view["history"][0]["from"] != initiator
    assert view["history"][0]["to"] == initiator
    assert view["history"][0]["body"] == "OWNER_RETURN_LAST:first_v1"
    assert view["history"][0]["message_id"] == "owner_return_2"
    snapshot = service.dialog.load(task)
    assert snapshot.messages[0].body == "old"
    replacement = service.append(initiator, task, head=returned["head"],
                                 message_id="first_v2", kind="task", body="new")
    view2 = service.view(task)
    assert view2["awaiting_replacement"] is False
    assert view2["active_message_id"] == "first_v2"
    assert view2["history"][2]["returned"] is True
    other = "chatgpt" if initiator == "mt" else "mt"
    service.append(other, task, head=replacement["commit"], message_id="ack_v2",
                   kind="report", body="yes")
    assert service.dialog.load(task).state.seq == 4


def test_return_head_and_last_message_are_strict(catalog):
    api, service = catalog
    created = service.create("chatgpt", "p325_strict", "Strict")
    task = created["task_id"]
    with pytest.raises(DialogError, match="return_unavailable"):
        service.return_turn(task, created["head"], "phantom")
    first = service.append("chatgpt", task, head=created["head"],
                           message_id="version_1", kind="task", body="old")
    with pytest.raises(DialogError, match="stale_head"):
        service.return_turn(task, created["head"], "version_1")
    with pytest.raises(DialogError, match="return_unavailable"):
        service.return_turn(task, first["commit"], "wrong_id")
    ret = service.return_turn(task, first["commit"], "version_1")
    with pytest.raises(DialogError, match="stale_head"):
        service.return_turn(task, first["commit"], "version_1")
    with pytest.raises(DialogError, match="return_unavailable"):
        service.return_turn(task, ret["head"], ret["message_id"] if "message_id" in ret else "owner_return_2")
    with pytest.raises(DialogError, match="gateway_message_kind_denied"):
        service.append("chatgpt", task, head=ret["head"],
                       message_id="fake_owner", kind="return", body="forged")


class _Auth:
    def verify(self, value):
        return value == "Basic owner-test"


class _Client:
    def __init__(self):
        self.requests = []
    def call(self, obj):
        self.requests.append(obj)
        if obj["op"] == "return_turn":
            return {"task_id": obj["task_id"], "head": "b"*40,
                    "returned_message_id": obj["last_message_id"],
                    "waiting_for": "mt", "created": True}
        raise AssertionError("unexpected request")


def _post(board, body, *, authorization="Basic owner-test", csrf=None, origin=None):
    raw = json.dumps(body).encode()
    env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/return_turn",
           "HTTP_HOST": "127.0.0.1:8787", "REMOTE_ADDR": "127.0.0.1",
           "HTTP_AUTHORIZATION": authorization,
           "HTTP_ORIGIN": board.origin if origin is None else origin,
           "HTTP_X_CSRF_TOKEN": board.csrf if csrf is None else csrf,
           "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)),
           "QUERY_STRING": "", "wsgi.input": io.BytesIO(raw)}
    codes = []
    result = b"".join(board(env, lambda code, hdrs: codes.append(code)))
    return int(codes[0].split()[0]), result


def test_taskboard_reuses_existing_owner_login_and_csrf():
    client = _Client()
    board = Taskboard(_Auth(), client)
    valid = {"task_id": "task_owner_test", "head": "a"*40,
             "last_message_id": "last_1"}
    assert _post(board, valid, authorization="Basic bad")[0] == 401
    assert _post(board, valid, csrf="bad")[0] == 409
    assert _post(board, valid, origin="http://attacker.test")[0] == 409
    assert client.requests == []
    status, response = _post(board, valid)
    assert status == 200
    assert json.loads(response)["created"] is True
    assert client.requests == [{**valid, "op": "return_turn",
                                "authorization": "Basic owner-test"}]
    assert _post(board, {**valid, "extra": "x"})[0] == 409
    assert len(client.requests) == 1


def test_runtime_rechecks_actual_owner_password_not_just_writer(catalog, monkeypatch):
    api, service = catalog
    created = service.create("mt", "p325_runtime", "Runtime")
    task = created["task_id"]
    first = service.append("mt", task, head=created["head"],
                           message_id="mt_v1", kind="report", body="old")
    common = CommonAuthority(service.dialog, service.registry, service.appends, task)
    runtime = Runtime(common, bridge_pid=123, catalog=service)
    class FakeVerifier:
        def __init__(self, path):
            self.path = path
        def verify(self, authorization):
            return authorization == "Basic owner-test"
    import git_dialog.service as auth_mod
    monkeypatch.setattr(auth_mod, "AuthAuthority", FakeVerifier)
    request = {"op": "return_turn", "task_id": task,
               "head": first["commit"], "last_message_id": "mt_v1",
               "authorization": "Basic bad"}
    with pytest.raises(DialogError, match="owner_return_denied"):
        runtime.call("mt", json.dumps(request).encode())
    with pytest.raises(DialogError, match="owner_return_denied"):
        runtime.call("chatgpt", json.dumps({**request,
                       "authorization": "Basic owner-test"}).encode())
    assert service.view(task)["history"][0]["message_id"] == "mt_v1"
    request["authorization"] = "Basic owner-test"
    result = runtime.call("mt", json.dumps(request).encode())
    assert result["waiting_for"] == "mt"
    assert service.view(task)["awaiting_replacement"] is True


def test_return_message_cannot_be_sent_by_peer(catalog):
    _, service = catalog
    created = service.create("chatgpt", "p325_peer", "Peer")
    task = created["task_id"]
    service.append("chatgpt", task, head=created["head"],
                   message_id="first", kind="task", body="old")
    for actor in ("chatgpt", "mt"):
        with pytest.raises(DialogError):
            service.append(actor, task, head=service.dialog.load(task).head,
                           message_id="owner_return_2", kind="return",
                           body="OWNER_RETURN_LAST:first")

@pytest.mark.parametrize("initiator", ["chatgpt", "mt"])
def test_return_event_is_not_chatgpt_notification(catalog, initiator):
    _, service = catalog
    created = service.create(initiator, "p325_notice_" + initiator, "Notice")
    task = created["task_id"]
    sent = service.append(initiator, task, head=created["head"],
                          message_id="normal_msg", kind="task", body="normal")
    service.return_turn(task, sent["commit"], "normal_msg")
    _, own = service.appends.notification_page(service.dialog, task)
    _, all_tasks = service.appends.notification_page_all(
        service.dialog, service.registry)
    assert all(not item["message_id"].startswith("owner_return_") for item in own)
    assert all(not item["message_id"].startswith("owner_return_") for item in all_tasks)
    assert [item["message_id"] for item in own] == (
        ["normal_msg"] if initiator == "mt" else []
    )

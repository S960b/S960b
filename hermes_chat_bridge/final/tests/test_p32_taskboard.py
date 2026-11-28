"""P32 board contracts: no real web services, credentials or Git traffic."""
import io
import json

import pytest

from git_dialog import DialogError
from git_dialog.taskboard import Taskboard
from test_dialog_catalog import catalog
from test_github import BASE, TASK, writes


@pytest.fixture(autouse=True)
def offline_github_quota(monkeypatch):
    """Taskboard HTTP tests must not read the live P36.1 API cache DB."""
    from git_dialog import api_budget
    monkeypatch.setattr(api_budget, "shared_status", lambda: {
        "source": "unavailable", "remaining": None, "limit": None,
        "reset_epoch": None, "paused": False,
    })


class Auth:
    def verify(self, token):
        return token == "Basic test"


class Client:
    def __init__(self):
        self.requests = []

    def call(self, request):
        self.requests.append(request)
        if request["op"] == "list_dialogs":
            return {"dialogs": [{"title": "Git A", "task_id": "task_offline",
                                  "branch": "dialogs/task_offline",
                                  "lifecycle": "active", "initiator": "mt"}],
                    "next_offset": None, "order": "newest"}
        if request["op"] == "view":
            return {"task_id": request["task_id"], "title": "Git A",
                    "head": "a"*40, "history": [
                        {"seq": 3, "message_id": "last", "body": "<script>x</script>"},
                        {"seq": 2, "message_id": "first", "body": "text"}],
                    "total_messages": 3, "next_offset": None, "status": "working"}
        if request["op"] == "delete_prepare":
            return {"task_id": request["task_id"], "confirmation": "x"*32,
                    "expected_head": request["head"], "expires_at": 12345,
                    "state": "closing"}
        if request["op"] == "delete_confirm":
            return {"task_id": request["task_id"], "head": request["head"],
                    "deleted": True, "remote_absent": True}
        raise AssertionError("unexpected operation")


def run(board, method, path, query="", body=b"", **headers):
    env = {"HTTP_HOST": "127.0.0.1:8787", "REMOTE_ADDR": "127.0.0.1",
           "HTTP_AUTHORIZATION": "Basic test", "REQUEST_METHOD": method,
           "PATH_INFO": path, "QUERY_STRING": query,
           "CONTENT_TYPE": headers.get("content_type", ""),
           "CONTENT_LENGTH": str(len(body)) if body else "",
           "HTTP_ORIGIN": headers.get("origin"),
           "HTTP_X_CSRF_TOKEN": headers.get("csrf", ""),
           "wsgi.input": io.BytesIO(body)}
    env.update(headers.get("env", {}))
    seen = []
    response = b"".join(board(env,lambda code, hdrs: seen.append((code,dict(hdrs)))))
    return int(seen[0][0].split()[0]), seen[0][1], response


def test_basic_auth_and_host_checks_never_reach_unix():
    client=Client()
    board=Taskboard(Auth(),client)
    assert run(board,"GET","/api/dialogs",env={"HTTP_AUTHORIZATION":""})[0]==401
    assert run(board,"GET","/api/dialogs",env={"HTTP_HOST":"attacker.test"})[0]==409
    assert run(board,"GET","/api/dialogs",env={"HTTP_X_FORWARDED_HOST":"evil"})[0]==409
    assert client.requests == []


def test_localhost_and_127_0_0_1_hosts_both_authorized_loopback_only():
    client=Client()
    board=Taskboard(Auth(),client)
    for host in ("127.0.0.1:8787", "localhost:8787"):
        code,_,_=run(board,"GET","/api/dialogs",env={"HTTP_HOST":host})
        assert code==200, host
    for host in ("evil.example", "127.0.0.2:8787", "localhost.evil:8787"):
        code,_,_=run(board,"GET","/api/dialogs",env={"HTTP_HOST":host})
        assert code==409, host
    assert client.requests  # 127.0.0.1 and localhost reached the data layer


def test_localhost_accepts_origin_and_post_still_requires_exact_loopback_origin():
    client=Client()
    board=Taskboard(Auth(),client)
    payload=json.dumps({"task_id":"task_offline","head":"a"*40}).encode()
    # POST with localhost Origin reaches the CSRF gate (not boundary-rejected).
    code,_,_=run(board,"POST","/api/delete_prepare",body=payload,
            content_type="application/json",origin="http://localhost:8787",csrf="bad")
    assert code==409 and not client.requests
    # A non-loopback Origin is rejected by the boundary before auth/CSRF.
    code,_,_=run(board,"GET","/api/dialogs",
            env={"HTTP_HOST":"127.0.0.1:8787","HTTP_ORIGIN":"http://evil.example"})
    assert code==409
    assert client.requests == []


def test_list_history_newest_first_and_static_xss_safe():
    client=Client()
    board=Taskboard(Auth(),client)
    code, headers, raw=run(board,"GET","/api/dialogs","offset=0&limit=30")
    assert code==200 and headers["X-Frame-Options"]=="DENY"
    obj=json.loads(raw)
    assert obj["csrf"]==board.csrf and obj["dialogs"][0]["title"]=="Git A"
    assert obj["github_api"]["source"] == "unavailable"
    assert obj["github_api"]["remaining"] is None
    code,_,raw=run(board,"GET","/api/history",
        "task_id=task_offline&offset=0&limit=20")
    assert code==200
    assert [x["message_id"] for x in json.loads(raw)["history"]]==["last","first"]
    assert client.requests[-1]["order"]=="newest"
    code,_,html=run(board,"GET","/")
    assert code==200 and b"taskboard.js" in html
    code,_,js=run(board,"GET","/taskboard.js")
    assert code==200 and b"innerHTML" not in js
    assert b"textContent" in js


def test_delete_requires_fresh_csrf_origin_exact_schema():
    client=Client()
    board=Taskboard(Auth(),client)
    payload=json.dumps({"task_id":"task_offline","head":"a"*40}).encode()
    for path in ("/api/delete_prepare", "/api/delete_confirm"):
        bad=run(board,"POST",path,body=payload,
                content_type="application/json",origin=board.origin,csrf="bad")[0]
        assert bad==409 and not client.requests
    bad=run(board,"POST","/api/delete_prepare",body=payload,
            content_type="application/json",csrf=board.csrf)[0]
    assert bad==409 and not client.requests
    bad=run(board,"POST","/api/delete_prepare",body=payload,
            content_type="application/json",origin=board.origin,csrf=board.csrf,
            env={"HTTP_X_FORWARDED_FOR":"1.2.3.4"})[0]
    assert bad==409 and not client.requests
    code,_,raw=run(board,"POST","/api/delete_prepare",body=payload,
         content_type="application/json",origin=board.origin,csrf=board.csrf)
    assert code==200 and json.loads(raw)["state"]=="closing"
    confirm=json.dumps({"task_id":"task_offline","head":"a"*40,
                        "confirmation":"x"*32}).encode()
    code,_,raw=run(board,"POST","/api/delete_confirm",body=confirm,
         content_type="application/json",origin=board.origin,csrf=board.csrf)
    assert code==200 and json.loads(raw)["remote_absent"] is True
    assert [x["op"] for x in client.requests]==["delete_prepare","delete_confirm"]


def test_delete_two_stage_removes_only_confirmed_git_ref(catalog, monkeypatch):
    api, service=catalog
    created=service.create("chatgpt","p32_delete_test","To delete")
    t=created["task_id"]
    receipt=service.append("chatgpt",t,head=created["head"],
        message_id="first",kind="task",body="History preserved in old commit")
    api.calls.clear()
    with pytest.raises(DialogError,match="stale_head"):
        service.delete_prepare(t,created["head"])
    assert service.registry.get(t)["state"]=="active"
    assert not writes(api)
    token=service.delete_prepare(t,receipt["commit"])
    assert token["task_id"]==t and token["state"]=="closing"
    assert service.registry.get(t)["state"]=="closing"
    ref="refs/heads/dialogs/"+t
    assert ref in api.refs
    original=api.request
    def fake_delete(method,path,payload=None):
        if method=="DELETE":
            assert path.endswith("/git/refs/heads/dialogs/"+t)
            api.calls.append((method,path,payload))
            assert ref in api.refs
            del api.refs[ref]
            return None
        return original(method,path,payload)
    monkeypatch.setattr(api,"request",fake_delete)
    result=service.delete_confirm(t,receipt["commit"],token["confirmation"])
    assert result=={"task_id":t,"head":receipt["commit"],
                    "deleted":True,"remote_absent":True}
    assert ref not in api.refs
    assert service.registry.get(t)["state"]=="tombstoned"
    assert service.registry.get(t)["remote_absent"]==1
    assert service.list()["dialogs"][0]["lifecycle"]=="tombstoned"
    assert len([x for x in api.calls if x[0]=="DELETE"])==1
    with pytest.raises(DialogError,match="dialog_not_writable"):
        service.append("mt",t,head=receipt["commit"],
            message_id="reject",kind="report",body="blocked")


def test_delete_wrong_nonce_never_calls_delete(catalog):
    api,service=catalog
    r=service.create("mt","p32_invalid_nonce","X")
    ticket=service.delete_prepare(r["task_id"],r["head"])
    api.calls.clear()
    with pytest.raises(DialogError,match="invalid_delete_confirmation"):
        service.delete_confirm(r["task_id"],r["head"],"bogus")
    assert not any(row[0]=="DELETE" for row in api.calls)
    assert service.registry.get(r["task_id"])["state"]=="closing"


def test_p32_light_visual_direction_and_instant_history_overlay():
    """Static UI contract; node --check supplies separate JS syntax validation."""
    from pathlib import Path
    from git_dialog import taskboard
    root = Path(taskboard.__file__).parent
    css = (root / "taskboard.css").read_text(encoding="utf-8")
    js = (root / "taskboard.js").read_text(encoding="utf-8")
    html = (root / "taskboard.html").read_text(encoding="utf-8")
    assert "color-scheme: light" in css
    assert "#f2f4f6" in css
    assert ".actor-mt" in css and ".actor-chatgpt" in css
    assert ".history-btn" in css
    assert 'role="dialog"' in html and 'id="loading-history"' in html
    assert "showHistory(task);" in js
    assert "CACHE_MS = 15000" in js and "firstPage(item.task_id)" in js
    assert "direction(last.from, last.to)" in js
    assert "direction(m.from, m.to)" in js
    assert "innerHTML" not in js


def test_p322_verified_in_memory_cache_and_head_invalidation(catalog):
    api, service = catalog
    created = service.create("chatgpt", "p322_cache", "P32 cache")
    task = created["task_id"]
    api.calls.clear()
    empty = service.dialog.load(task)
    assert empty.head == created["head"]
    assert len(api.calls) >= 6  # initial cold commit/tree/blobs
    api.calls.clear()
    again = service.dialog.load(task)
    assert again is empty
    assert len(api.calls) == 2
    assert api.calls[0][1] == BASE
    assert "/git/ref/heads/dialogs/" in api.calls[1][1]
    receipt = service.append("chatgpt", task, head=created["head"],
                             message_id="warm_1", kind="task", body="New value")
    # append reconciles its newly published commit through load(), warming
    # the verified cache. Repeating that view SHOULD need only repo+ref GETs.
    api.calls.clear()
    fresh = service.dialog.load(task)
    assert fresh.head == receipt["commit"] and fresh is not empty
    assert fresh.last.message_id == "warm_1"
    assert len(api.calls) == 2
    api.calls.clear()
    assert service.dialog.load(task) is fresh
    assert len(api.calls) == 2

    # Simulate a valid independent peer writing to the same Git ref.
    # Its separate GitHubDialog instance cannot populate our cache.
    from git_dialog.github import GitHubDialog
    remote_peer = GitHubDialog(api)
    external = remote_peer.append(task, receipt["commit"],
                                  message_id="external_mt_2", actor="mt",
                                  kind="report", body="independent change")
    api.calls.clear()
    updated = service.dialog.load(task)
    assert updated is not fresh
    assert updated.head == external.commit_sha
    assert updated.last.message_id == "external_mt_2"
    assert len(api.calls) >= 6  # new HEAD forces immutable commit/tree/blob reads
    api.calls.clear()
    assert service.dialog.load(task) is updated
    assert len(api.calls) == 2
    # Cache never bypasses permission and branch-existence checks.
    api.private = False
    with pytest.raises(DialogError, match="private_repository_required"):
        service.dialog.load(task)
    api.private = True
    del api.refs["refs/heads/dialogs/" + task]
    with pytest.raises(DialogError, match="branch_missing_or_inaccessible"):
        service.dialog.load(task)


def test_p322_cache_is_bounded_and_is_not_disk_persistent(catalog):
    from git_dialog.github import SNAPSHOT_CACHE_ENTRIES
    api, service = catalog
    for number in range(SNAPSHOT_CACHE_ENTRIES + 3):
        created = service.create("mt", "p322_bound_" + str(number), "Task " + str(number))
        service.dialog.load(created["task_id"])
    assert len(service.dialog._snapshots) == SNAPSHOT_CACHE_ENTRIES
    assert all(isinstance(k, tuple) and len(k) == 2 for k in service.dialog._snapshots)
    # A second service instance has an empty process-local cache.
    from git_dialog.github import GitHubDialog
    assert len(GitHubDialog(api)._snapshots) == 0


def test_p322_bounded_parallel_transport_preserves_validated_snapshot(catalog):
    import threading
    import time
    from git_dialog.github import JsonHttp, MAX_PARALLEL_BLOBS
    api, service = catalog
    created = service.create("chatgpt", "p322_parallel", "Parallel blobs")
    task, head = created["task_id"], created["head"]
    for n in range(8):
        actor = "chatgpt" if n % 2 == 0 else "mt"
        result = service.append(actor, task, head=head,
                                message_id="parallel_" + str(n),
                                kind="task" if actor == "chatgpt" else "report",
                                body="Message " + str(n))
        head = result["commit"]

    class ParallelTransport(JsonHttp):
        def __init__(self, inner):
            self.inner = inner
            self.lock = threading.Lock()
            self.concurrent = 0
            self.peak = 0

        def request(self, method, path, payload=None):
            if method == "GET" and "/git/blobs/" in path:
                with self.lock:
                    self.concurrent += 1
                    self.peak = max(self.peak, self.concurrent)
                try:
                    time.sleep(0.015)
                    return self.inner.request(method, path, payload)
                finally:
                    with self.lock:
                        self.concurrent -= 1
            return self.inner.request(method, path, payload)

    transport = ParallelTransport(api)
    from git_dialog.github import GitHubDialog
    fast = GitHubDialog(transport)
    snap = fast.load(task)
    assert snap.head == head
    assert len(snap.messages) == 8
    assert [m.seq for m in snap.messages] == list(range(1, 9))
    assert 2 <= transport.peak <= MAX_PARALLEL_BLOBS
    peak_before = transport.peak
    api.calls.clear()
    assert fast.load(task) is snap
    assert len(api.calls) == 2
    assert transport.peak == peak_before

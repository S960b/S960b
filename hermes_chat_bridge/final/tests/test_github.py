"""Offline REST contract double; never contacts GitHub or discovers credentials."""
import base64
import copy
import hashlib
import json
import threading

import pytest

from git_dialog import DialogError, TaskState
from git_dialog.github import ApiError, GitHubDialog, JsonHttp, canonical, decode_json

TASK = "task_offline"
BASE = "/repos/S960b/agent-dialogs"
REF = "refs/heads/dialogs/" + TASK
STAMP = "2026-10-26T15:00:00Z"


def object_sha(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class FakeApi:
    """Models documented FF/update-existing behavior; NOT proof of live behavior."""
    def __init__(self):
        self.calls = []
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.private = True
        self.refs = {}
        self.hook = None
        self.lock = threading.Lock()
        entries = {}
        for path, obj in {
            "task.json": {"protocol": "git-dialog", "version": "1", "task_id": TASK, "title": "offline"},
            "state.json": TaskState(TASK, 0, 0, None, "chatgpt", "waiting").to_dict(),
            "README.md": {"keep": "unchanged"},
        }.items():
            entries[path] = self.entry(canonical(obj))
        self.initial = self.commit(self.tree(entries), [])
        self.refs[REF] = self.initial

    def entry(self, content):
        raw = content.encode("utf-8")
        s = object_sha("blob", raw)
        self.blobs[s] = {"sha": s, "encoding": "base64", "size": len(raw),
                         "content": base64.b64encode(raw).decode()}
        return {"type": "blob", "mode": "100644", "sha": s}

    def tree(self, entries):
        s = object_sha("tree", canonical(entries).encode())
        self.trees[s] = copy.deepcopy(entries)
        return s

    def commit(self, tree, parents):
        s = object_sha("commit", canonical([tree, parents]).encode())
        self.commits[s] = {"sha": s, "tree": {"sha": tree},
                           "parents": [{"sha": p} for p in parents]}
        return s

    def request(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        assert path.startswith(BASE)
        suffix = path[len(BASE):]
        if self.hook:
            self.hook(method, suffix, payload)
        if method == "GET" and suffix == "":
            return {"private": self.private, "full_name": "S960b/agent-dialogs"}
        if method == "GET" and suffix.startswith("/git/ref/"):
            ref = "refs/" + suffix.removeprefix("/git/ref/")
            if ref not in self.refs:
                raise ApiError(404)
            return {"ref": ref, "object": {"type": "commit", "sha": self.refs[ref]}}
        if method == "GET" and suffix.startswith("/git/commits/"):
            return copy.deepcopy(self.commits[suffix.rsplit("/", 1)[1]])
        if method == "GET" and suffix.startswith("/git/trees/"):
            s = suffix.rsplit("/", 1)[1].split("?")[0]
            return {"sha": s, "truncated": False,
                    "tree": [{"path": p, **e} for p, e in self.trees[s].items()]}
        if method == "GET" and suffix.startswith("/git/blobs/"):
            return copy.deepcopy(self.blobs[suffix.rsplit("/", 1)[1]])
        if method == "POST" and suffix == "/git/trees":
            entries = copy.deepcopy(self.trees[payload["base_tree"]])
            assert len(payload["tree"]) == 2
            for e in payload["tree"]:
                assert e["mode"] == "100644" and e["type"] == "blob"
                entries[e["path"]] = self.entry(e["content"])
            return {"sha": self.tree(entries)}
        if method == "POST" and suffix == "/git/commits":
            return {"sha": self.commit(payload["tree"], payload["parents"])}
        if method == "PATCH" and suffix.startswith("/git/refs/"):
            assert payload["force"] is False
            ref = "refs/" + suffix.removeprefix("/git/refs/")
            with self.lock:
                if ref not in self.refs:
                    raise ApiError(422)
                current = self.refs[ref]
                new = payload["sha"]
                if [p["sha"] for p in self.commits[new]["parents"]] != [current]:
                    raise ApiError(422)
                self.refs[ref] = new
            return {"ref": ref, "object": {"type": "commit", "sha": new}}
        raise AssertionError((method, suffix))


def append(api, mid="m1", **kw):
    return GitHubDialog(api).append(TASK, api.initial, message_id=mid, actor="chatgpt",
                                    kind="task", body="do not execute this text", created_at_utc=STAMP, **kw)


def writes(api):
    return [c for c in api.calls if c[0] != "GET"]


def test_atomic_append_preserves_old_files_and_confirms_exact_message():
    api = FakeApi()
    receipt = append(api)
    snap = GitHubDialog(api).load(TASK)
    assert receipt.created and receipt.commit_sha == snap.head
    assert snap.messages == [receipt.message] and snap.state.next_actor == "mt"
    assert [c[0] for c in writes(api)] == ["POST", "POST", "PATCH"]
    old = api.trees[api.commits[api.initial]["tree"]["sha"]]
    new = api.trees[api.commits[snap.head]["tree"]["sha"]]
    assert old["README.md"] == new["README.md"]
    assert old["task.json"] == new["task.json"]
    assert "messages/1.chatgpt.json" in new
    assert not any(p.startswith("dialogs/") for p in new)


def test_repeated_message_is_read_only():
    api = FakeApi()
    r = append(api)
    api.calls.clear()
    again = GitHubDialog(api).append(TASK, r.commit_sha, message_id="m1", actor="chatgpt",
                                     kind="task", body=r.message.body)
    assert not again.created and again.message == r.message
    assert not writes(api)


@pytest.mark.parametrize("case,code", [("deleted", "branch_missing_or_inaccessible"),
                                      ("stale", "stale_head"),
                                      ("public", "private_repository_required")])
def test_preflight_refusals_never_write(case, code):
    api = FakeApi()
    if case == "deleted":
        api.refs.clear()
    elif case == "stale":
        api.refs[REF] = "a" * 40
    else:
        api.private = False
    with pytest.raises(DialogError, match=code):
        append(api)
    assert not writes(api)


def test_delete_between_preflight_and_patch_does_not_recreate_ref():
    api = FakeApi()
    def delete(method, suffix, payload):
        if method == "PATCH":
            api.refs.clear()
    api.hook = delete
    with pytest.raises(DialogError, match="ref_update_rejected"):
        append(api)
    assert not api.refs
    assert not any(m == "POST" and p.endswith("/git/refs") for m, p, _ in api.calls)


def test_two_siblings_only_one_published():
    api = FakeApi()
    barrier = threading.Barrier(2)
    api.hook = lambda method, *args: barrier.wait(timeout=3) if method == "PATCH" else None
    outcomes = []
    def writer(mid):
        try:
            outcomes.append(append(api, mid))
        except DialogError as exc:
            outcomes.append(exc.code)
    threads = [threading.Thread(target=writer, args=(mid,)) for mid in ("one", "two")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert outcomes.count("ref_update_rejected") == 1
    assert len(GitHubDialog(api).load(TASK).messages) == 1


def test_lost_patch_response_is_unknown_and_read_only_reconciliation():
    api = FakeApi()
    real = api.request
    def lost(method, path, payload=None):
        result = real(method, path, payload)
        if method == "PATCH":
            raise ApiError()  # accepted by server; ACK lost
        return result
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"):
        append(api)
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1
    snap = GitHubDialog(api).load(TASK)
    api.calls.clear()
    found = GitHubDialog(api).reconcile(TASK, snap.last)
    assert found.message == snap.last and not writes(api)


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 429, 500])
def test_patch_errors_no_automatic_retry(status):
    api = FakeApi()
    def fail(method, *args):
        if method == "PATCH":
            raise ApiError(status)
    api.hook = fail
    with pytest.raises(DialogError, match="publication_unknown" if status == 500 else "ref_update_rejected"):
        append(api)
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 1
    assert api.refs[REF] == api.initial


@pytest.mark.parametrize("damage", ["hash", "encoding", "symlink", "filename", "extra_message"])
def test_corrupt_remote_input_cannot_be_published(damage):
    api = FakeApi()
    tree = api.trees[api.commits[api.initial]["tree"]["sha"]]
    entry = tree["task.json"]
    if damage == "hash":
        api.blobs[entry["sha"]]["content"] = base64.b64encode(b"{}").decode()
    elif damage == "encoding":
        api.blobs[entry["sha"]]["encoding"] = "utf-8"
    elif damage == "symlink":
        entry["mode"] = "120000"
    elif damage == "filename":
        tree["../bad"] = entry
    else:
        tree["messages/unexpected.json"] = api.entry("{}")
    with pytest.raises(DialogError):
        append(api)
    assert not writes(api)


def test_candidate_extra_file_is_rejected_before_ref_update():
    api = FakeApi()
    real = api.request
    def tamper(method, path, payload=None):
        result = real(method, path, payload)
        if method == "POST" and path.endswith("/git/trees"):
            api.trees[result["sha"]]["UNEXPECTED"] = api.entry("{}")
        return result
    api.request = tamper
    with pytest.raises(DialogError, match="candidate_content_mismatch"):
        append(api)
    assert not any(c[0] == "PATCH" for c in api.calls)


@pytest.mark.parametrize("parents", [[], [{"sha": "f" * 40}],
                                      [{"sha": "f" * 40}, {"sha": "e" * 40}], None])
def test_candidate_must_have_one_expected_parent(parents):
    api = FakeApi()
    real = api.request
    def tamper(method, path, payload=None):
        result = real(method, path, payload)
        if method == "POST" and path.endswith("/git/commits"):
            api.commits[result["sha"]]["parents"] = parents
        return result
    api.request = tamper
    with pytest.raises(DialogError, match="invalid_commit_parent"):
        append(api)
    assert not any(c[0] == "PATCH" for c in api.calls)


def test_swapped_message_blobs_rejected():
    api = FakeApi()
    r = append(api)
    GitHubDialog(api).append(TASK, r.commit_sha, message_id="m2", actor="mt",
                             kind="report", body="test report", created_at_utc=STAMP)
    entries = api.trees[api.commits[api.refs[REF]]["tree"]["sha"]]
    one, two = "messages/1.chatgpt.json", "messages/2.mt.json"
    entries[one], entries[two] = entries[two], entries[one]
    with pytest.raises(DialogError, match="invalid_message_paths"):
        GitHubDialog(api).load(TASK)


def test_truncated_tree_and_wrong_ref_fail_closed():
    for mode in ("truncated", "ref"):
        api = FakeApi()
        real = api.request
        def tamper(method, path, payload=None):
            result = real(method, path, payload)
            if mode == "truncated" and "/git/trees/" in path:
                result["truncated"] = True
            if mode == "ref" and "/git/ref/" in path:
                result["ref"] = "refs/heads/main"
            return result
        api.request = tamper
        with pytest.raises(DialogError):
            append(api)
        assert not writes(api)


def test_move_after_candidate_verification_prevents_patch():
    api = FakeApi()
    count = 0
    def move(method, suffix, payload):
        nonlocal count
        if method == "GET" and suffix.startswith("/git/ref/"):
            count += 1
            if count == 2:
                api.refs[REF] = "f" * 40
    api.hook = move
    with pytest.raises(DialogError, match="stale_head"):
        append(api)
    assert not any(c[0] == "PATCH" for c in api.calls)


def test_ack_without_observed_message_is_not_confirmation():
    api = FakeApi()
    real = api.request
    def lie(method, path, payload=None):
        if method == "PATCH":
            # Success-shaped response but ref did NOT change.
            return {"ref": REF, "object": {"sha": payload["sha"]}}
        return real(method, path, payload)
    api.request = lie
    with pytest.raises(DialogError, match="publication_not_observed"):
        append(api)


def test_json_http_success_and_bound_read(monkeypatch):
    import git_dialog.github as module
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self, limit):
            assert limit == module.LIMIT + 1
            return b'{"private":true}'
    class Opener:
        def open(self, req, timeout):
            assert req.headers["Authorization"] == "Bearer nonsecret-test"
            return Response()
    monkeypatch.setattr(module, "build_opener", lambda *a: Opener())
    assert JsonHttp("nonsecret-test").request("GET", BASE) == {"private": True}


@pytest.mark.parametrize("payload", [b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'])
def test_strict_json(payload):
    with pytest.raises(DialogError, match="invalid_json"):
        decode_json(payload)


@pytest.mark.parametrize("token", [None, "", "bad\ntoken", "bad token"])
def test_missing_auth_never_discovers_credentials(token, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-read-this")
    with pytest.raises(DialogError, match="authorization_required"):
        JsonHttp(token)


def test_http_transport_fixed_host_sanitized_error_no_retry(monkeypatch):
    import git_dialog.github as module
    from urllib.error import URLError
    seen = []
    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            raise URLError("exception containing a sensitive value")
    monkeypatch.setattr(module, "build_opener", lambda *a: Opener())
    transport = JsonHttp("nonsecret-test-sentinel")
    with pytest.raises(ApiError) as exc:
        transport.request("GET", BASE)
    assert str(exc.value) == "github_transport_error"
    assert "sentinel" not in repr(transport)
    assert len(seen) == 1 and seen[0][0].full_url == "https://api.github.com" + BASE
    assert isinstance(module.NoRedirect(), module.HTTPRedirectHandler)
    assert module.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None
    for path in ("https://evil.invalid", "//evil.invalid", "/repos/a/../evil", BASE + "\n"):
        with pytest.raises(DialogError):
            transport.request("GET", path)
    assert len(seen) == 1


@pytest.mark.parametrize("repository", ["https://github.com/x/y", "../repo", "S960b/..", "a/b/c"])
def test_repository_injection(repository):
    with pytest.raises(DialogError, match="invalid_repository"):
        GitHubDialog(FakeApi(), repository)

"""P36.1 offline contract: quota safety and private cross-process Git GET cache."""
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from git_dialog import DialogError
from git_dialog.api_budget import (
    DEFAULT_CACHE, RESERVE, SharedApiBudget, _immutable, _mutable, shared_status,
)
from git_dialog.github import ApiError, JsonHttp
import git_dialog.github as github


REF = "/repos/S960b/agent-dialogs/git/ref/heads/dialogs/task_" + "a"*32
REPO = "/repos/S960b/agent-dialogs"
BLOB = "/repos/S960b/agent-dialogs/git/blobs/" + "b"*40
TREE = "/repos/S960b/agent-dialogs/git/trees/" + "c"*40 + "?recursive=1"


def private_db(tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path / "budget.sqlite"


def hdr(*, remaining=4900, limit=5000, reset=99999, etag=None):
    h = Message()
    h["x-ratelimit-remaining"] = str(remaining)
    h["x-ratelimit-limit"] = str(limit)
    h["x-ratelimit-reset"] = str(reset)
    if etag is not None:
        h["ETag"] = etag
    return h


def test_allowed_paths_only():
    assert _immutable(BLOB) and _immutable(TREE)
    assert _mutable(REF) and _mutable(REPO)
    assert not _immutable(REF) and not _mutable(BLOB)
    assert not _mutable("/repos/unknown/foo")
    assert not _immutable("/repos/S960b/agent-dialogs/git/blobs/" + "x"*40)


def test_shared_cache_across_distinct_instances_and_token_isolation(tmp_path):
    path = private_db(tmp_path)
    a = SharedApiBudget(path, "test-secret-1", clock=lambda: 1000)
    a.observe(hdr(reset=4000,etag='"v1"'), status=200, path=BLOB, data='{"sha":"b"}')
    assert a.cached(BLOB) == ('"v1"', '{"sha":"b"}')
    assert SharedApiBudget(path, "test-secret-1", clock=lambda: 1000).cached(BLOB) == ('"v1"', '{"sha":"b"}')
    assert SharedApiBudget(path, "test-secret-2", clock=lambda: 1000).cached(BLOB) is None
    assert "test-secret-1" not in path.read_bytes().decode("utf8", errors="ignore")


def test_quota_stops_before_exhaustion_and_recovers_after_reset(tmp_path):
    path = private_db(tmp_path)
    tick = [1000]
    a = SharedApiBudget(path, "test-a", clock=lambda:tick[0])
    a.observe(hdr(remaining=RESERVE, reset=2000), status=200)
    with pytest.raises(DialogError,match="github_rate_limited"):
        a.gate()
    tick[0] = 2001
    a.gate()


def test_primary_rate_limit_and_secondary_retry_after(tmp_path):
    path = private_db(tmp_path)
    a = SharedApiBudget(path, "test-a", clock=lambda:1000)
    a.observe(hdr(remaining=0,reset=1600),status=403)
    with pytest.raises(DialogError,match="github_rate_limited"):
        a.gate()
    assert a.status()["remaining"] == 0
    assert a.status()["paused"] is True

    b = SharedApiBudget(path,"another-key",clock=lambda:1000)
    headers = hdr(remaining=4500,reset=1600)
    headers["Retry-After"]="120"
    b.observe(headers,status=429)
    with pytest.raises(DialogError,match="github_rate_limited"):
        b.gate()


def test_status_reader_does_not_create_file_or_expose_token(tmp_path):
    path = private_db(tmp_path)
    assert shared_status(path)["source"] == "unavailable"
    assert not path.exists()
    a = SharedApiBudget(path,"secret-only-in-memory",clock=lambda:1000)
    a.observe(hdr(remaining=4321,reset=4000),status=200)
    public = shared_status(path)
    assert set(public)=={"source","remaining","limit","reset_epoch","paused"}
    assert public["remaining"] == 4321
    assert "secret" not in str(public)


def test_unsafe_cache_file_rejected(tmp_path):
    path = private_db(tmp_path)
    path.write_bytes(b"")
    path.chmod(0o644)
    with pytest.raises(DialogError,match="github_cache_file_rejected"):
        SharedApiBudget(path,"test-secret")


class Response:
    def __init__(self, payload, headers, status=200):
        self.body = payload.encode("utf8")
        self.headers = headers
        self.status = status
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def read(self,limit): return self.body[:limit]


def test_conditional_304_validates_origin_and_reuses_cached_ref(tmp_path,monkeypatch):
    path = private_db(tmp_path)
    header = hdr(remaining=4998,reset=9999999999,etag='"ref-1"')
    # Github's 304 response is raised by urllib as HTTPError.
    calls = []
    class Opener:
        def open(self,req,timeout):
            calls.append(dict(req.header_items()))
            if len(calls)==1:
                return Response('{"ok":true}',header)
            raise HTTPError(req.full_url,304,"Not Modified",header,None)
    monkeypatch.setattr(github,"build_opener",lambda *a:Opener())
    client = JsonHttp("test-secret",budget_path=path)
    assert client.request("GET",REF) == {"ok":True}
    assert client.request("GET",REF) == {"ok":True}
    assert len(calls)==2
    assert calls[1].get("If-none-match") == '"ref-1"'


def test_immutable_object_second_read_uses_private_cache(tmp_path,monkeypatch):
    path=private_db(tmp_path)
    calls=[]
    class Opener:
        def open(self,req,timeout):
            calls.append(req.full_url)
            return Response('{"sha":"cached-immutable"}',
                            hdr(remaining=4999,reset=9999999999))
    monkeypatch.setattr(github,"build_opener",lambda *a:Opener())
    client=JsonHttp("test-secret",budget_path=path)
    assert client.request("GET",TREE) == {"sha":"cached-immutable"}
    assert client.request("GET",TREE) == {"sha":"cached-immutable"}
    assert len(calls)==1


def test_403_without_rate_headers_is_not_mislabeled(tmp_path,monkeypatch):
    path=private_db(tmp_path)
    class Opener:
        def open(self,req,timeout):
            raise HTTPError(req.full_url,403,"Forbidden",Message(),None)
    monkeypatch.setattr(github,"build_opener",lambda *a:Opener())
    client=JsonHttp("test-secret",budget_path=path)
    with pytest.raises(ApiError) as err:
        client.request("GET",REPO)
    assert err.value.status==403


def test_cached_ref_never_used_on_403(tmp_path,monkeypatch):
    path=private_db(tmp_path)
    a=SharedApiBudget(path,"test-secret")
    a.observe({"etag":'"valid"'},status=200,path=REF,data='{"ok":true}')
    class Opener:
        def open(self,req,timeout):
            raise HTTPError(req.full_url,403,"Forbidden",Message(),None)
    monkeypatch.setattr(github,"build_opener",lambda *a:Opener())
    with pytest.raises(ApiError):
        JsonHttp("test-secret",budget_path=path).request("GET",REF)

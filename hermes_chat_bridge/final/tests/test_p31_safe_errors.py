"""P31 end-to-end sanitized error classification across gateway/IPC."""
import asyncio
import json
import os
import stat
from types import SimpleNamespace

import pytest

from git_dialog import DialogError
from git_dialog import native_gateway, unix_authority
from git_dialog.p31_errors import error_value, public_code
from test_unix_authority import Channel


@pytest.mark.parametrize(("internal", "external"), [
    ("wrong_turn", "wrong_turn"),
    ("stale_head", "head_mismatch"),
    ("authority_writer_denied", "peer_denied"),
    ("catalog_request_rejected", "invalid_arguments"),
    ("message_id_conflict", "conflict"),
])
def test_authority_dispatch_safe_error(internal, external, monkeypatch):
    # A valid authenticated peer has already passed the Unix ACL.
    def fail(*args):
        raise DialogError(internal, "secret=NEVER_EXPOSE")
    fake = SimpleNamespace(uid=os.getuid(), bridge_pid=101, call=fail)
    conn = Channel({"op": "view", "task_id": "task_" + "a" * 32,
                    "offset": 0, "limit": 2, "order": "oldest"}, pid=101)
    unix_authority.Runtime.handle(fake, "chatgpt", conn)
    result = conn.result()
    assert result["ok"] is False
    assert result["result"] == {
        "error": "authority_result_unconfirmed",
        "retry_authorized": False,
        "phase": "dispatch", "code": external,
    }
    assert "NEVER_EXPOSE" not in json.dumps(result)


def test_unexpected_errors_remain_opaque():
    def fail(*args): raise RuntimeError("SENSITIVE_SECRET")
    fake = SimpleNamespace(uid=os.getuid(), bridge_pid=101, call=fail)
    conn = Channel({"op": "view", "task_id": "task_" + "a" * 32,
                    "offset": 0, "limit": 2, "order": "oldest"}, pid=101)
    unix_authority.Runtime.handle(fake, "chatgpt", conn)
    assert conn.result() == {"ok": False, "result": {
        "error": "authority_result_unconfirmed",
        "retry_authorized": False, "phase": "dispatch",
    }}


def test_unix_client_preserves_only_allowlisted_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(unix_authority, "_directory", lambda p: p)
    path = tmp_path / "authority.sock"
    monkeypatch.setattr(type(path), "lstat", lambda self: SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o600, st_uid=os.getuid(), st_nlink=1))
    class Connection:
        def settimeout(self, _): pass
        def connect(self, _): pass
        def sendall(self, _): pass
        def close(self): pass
    monkeypatch.setattr(unix_authority.socket, "socket", lambda *_: Connection())
    monkeypatch.setattr(unix_authority, "peer_identity", lambda _: (101, os.getuid()))
    def result(code):
        monkeypatch.setattr(unix_authority, "_frame", lambda *_args: json.dumps({
            "ok": False, "result": {"error": "authority_result_unconfirmed",
                "retry_authorized": False, "phase": "dispatch", "code": code}
        }).encode("utf-8"))
        with pytest.raises(DialogError) as exc:
            unix_authority.UnixClient(path).call({
                "op": "view", "task_id": "task_" + "a" * 32,
                "offset": 0, "limit": 2, "order": "oldest",
            })
        return exc.value
    assert result("wrong_turn").code == "wrong_turn"
    assert result("head_mismatch").code == "head_mismatch"
    assert result("LEAK_SECRET").code == "authority_result_unconfirmed"


def test_native_tool_returns_redacted_error_after_auth(monkeypatch):
    monkeypatch.setattr(native_gateway.UnixClient, "call", lambda self, _:
        (_ for _ in ()).throw(DialogError("wrong_turn", "TOKEN=secret")))
    class MCP:
        tools = {}
        def tool(self, **kwargs):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register
    class Bridge:
        def require_owner(self): return "known-oauth-principal"
    mcp = MCP()
    native_gateway.register_mcp(mcp, Bridge())
    reply = asyncio.run(mcp.tools["dialog_index"]("task_" + "a" * 32))
    assert reply == {"ok": False, "code": "wrong_turn", "retry_authorized": False}
    assert error_value("UNTRUSTED_SECRET")["code"] == "authority_unavailable"
    assert public_code(RuntimeError("secret")) is None

#!/usr/bin/env python3
"""P35 offline integration checks for an isolated staged git_dialog package."""
import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile

TASK = "task_offline"

class Auth:
    def verify(self, token):
        return token == "Basic offline"

class Client:
    def __init__(self):
        self.requests = []

    def call(self, request):
        self.requests.append(request)
        if request["op"] == "list_dialogs":
            return {"dialogs": [{
                "title": "Synthetic", "task_id": TASK, "initiator": "chatgpt",
                "lifecycle": "active", "branch": "dialogs/" + TASK}],
                "next_offset": None, "order": "newest"}
        raise AssertionError("unexpected request")

def wsgi_get(board, token):
    env = {"HTTP_HOST": "127.0.0.1:8787", "REMOTE_ADDR": "127.0.0.1",
           "HTTP_AUTHORIZATION": token, "REQUEST_METHOD": "GET",
           "PATH_INFO": "/api/dialogs", "QUERY_STRING": "offset=0&limit=1",
           "HTTP_ORIGIN": None, "wsgi.input": io.BytesIO()}
    header = []
    body = b"".join(board(env, lambda status, headers: header.append(status)))
    return int(header[0].split()[0]), body

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("isolated_src", type=Path)
    args = parser.parse_args()
    root = args.isolated_src.resolve()
    assert (root / "git_dialog" / "taskboard.py").is_file()
    sys.path.insert(0, str(root))
    from git_dialog import taskboard as tb
    assert Path(tb.__file__).resolve() == root / "git_dialog" / "taskboard.py"
    from git_dialog.taskboard import mt_delivery_snapshot
    with tempfile.TemporaryDirectory(prefix="p35-local-") as scratch:
        state = Path(scratch) / "state.json"
        state.write_text(json.dumps({
            "seen": {TASK: 3, "task_other": 10},
            "inflight": {TASK: {"seq": 4, "message_id": "synthetic", "phase": "prompt_attempted"}},
            "sessions": {TASK: "SENSITIVE_SESSION_ID_NOT_FOR_UI"}
        }), encoding="utf-8")
        state.chmod(0o600)
        data = mt_delivery_snapshot([TASK], state)
        assert data[TASK] == {"source": "ok", "seen_seq": 3, "inflight_seq": 4}
        assert "sessions" not in json.dumps(data)
        assert "SENSITIVE_SESSION_ID" not in json.dumps(data)
        assert mt_delivery_snapshot([TASK], Path(scratch) / "missing")[TASK]["source"] == "unavailable"
        symlink = Path(scratch) / "symlink.json"
        symlink.symlink_to(state)
        assert mt_delivery_snapshot([TASK], symlink)[TASK]["source"] == "unavailable"
        state.write_text('{"seen": {"task_offline": true}, "inflight": {}}', encoding="utf-8")
        assert mt_delivery_snapshot([TASK], state)[TASK]["seen_seq"] is None
        state.write_text(json.dumps({"seen": {TASK: 5}, "inflight": {}}), encoding="utf-8")
        original = tb.MT_STATE
        try:
            tb.MT_STATE = state
            client = Client()
            board = tb.Taskboard(Auth(), client)
            denied, _ = wsgi_get(board, "Basic wrong")
            assert denied == 401 and not client.requests
            status, raw = wsgi_get(board, "Basic offline")
            assert status == 200
            result = json.loads(raw)
            assert result["mt_delivery"][TASK] == {
                "source": "ok", "seen_seq": 5, "inflight_seq": None}
            assert result["dialogs"][0]["task_id"] == TASK
            assert "sessions" not in raw.decode("utf-8")
            assert client.requests == [{"op": "list_dialogs", "offset": 0, "limit": 1}]
        finally:
            tb.MT_STATE = original

    html = (root / "git_dialog" / "taskboard.html").read_text(encoding="utf-8")
    js = (root / "git_dialog" / "taskboard.js").read_text(encoding="utf-8")
    assert html.count("<th>Последнее действие</th>") == 1
    assert "tr.append(title, job, status, lastAct, actions)" in js
    assert "item._lastAct.textContent = action.label" in js
    assert "innerHTML" not in js and "setInterval(" not in js
    print("P35_OFFLINE_PASS")

if __name__ == "__main__":
    main()

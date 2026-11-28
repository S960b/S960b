"""Release acceptance: full component chain, synthetic authority and FakeApi only."""
import json

import pytest

from git_dialog import DialogError
from git_dialog.github import ApiError
from git_dialog.journal import ExecutionJournal
from git_dialog.panel import render_panel
from git_dialog.worker import WorkerStep
import git_dialog.worker as worker_module
from test_ingress import setup, request
from test_confirmation import cleanup
from test_github import TASK, writes


PING = '{"protocol":"git-dialog-action","version":1,"op":"ping"}'


def send(dispatcher, grant, head, body=PING):
    return request(dispatcher, grant, {"op": "append", "head": head,
        "message_id": "acceptance_request", "kind": "task", "body": body})


def test_complete_scoped_request_worker_panel_and_explicit_terminal_intent(tmp_path):
    api, registry, box, store, authority, dispatcher = setup(tmp_path)
    sender = authority.issue("chatgpt_worker", now=100)
    owner = authority.issue("configured_owner", now=100)
    sent = send(dispatcher, sender, api.initial)
    journal = ExecutionJournal(tmp_path / "execution.sqlite")
    try:
        worker = WorkerStep(journal, dispatcher.gateway.port("mt"), registry, TASK, allowed_ops=("ping",))
        result = worker.step("acceptance_request", "acceptance_report", "acceptance_run")
        assert json.loads(result["result"])["value"] == "pong"
        current = dispatcher.gateway.load(TASK)
        view = request(dispatcher, owner, {"op": "view", "offset": 0, "limit": 20})
        assert view["total_messages"] == 2 and view["waiting_for"] == "chatgpt"
        html = render_panel(current).decode()
        assert "acceptance_report" in html and 'type="button" disabled' in html
        pending = request(dispatcher, owner, {"op": "request_close", "head": current.head})
        request(dispatcher, owner, {"op": "cancel_close", "nonce": pending["confirmation"]})
        assert registry.get(TASK)["state"] == "active"
        pending = request(dispatcher, owner, {"op": "request_close", "head": current.head})
        request(dispatcher, owner, {"op": "confirm_close", "nonce": pending["confirmation"]})
        assert not box.get(TASK)["attempted"]
        assert dispatcher.gateway.load(TASK).state.status == "working"
        # Separate trusted call: never exposed as RPC. Offline ONLY.
        coordinator = dispatcher.confirmation.coordinator
        closed = coordinator.publish()
        assert closed["confirmed_commit"] == dispatcher.gateway.load(TASK).head
        assert dispatcher.gateway.load(TASK).state.status == "done"
        api.calls.clear()
        coordinator.observe()
        with pytest.raises(DialogError):
            send(dispatcher, sender, closed["confirmed_commit"])
        assert not writes(api)
        assert not any(call[0] == "DELETE" for call in api.calls)
    finally:
        journal.close(); cleanup(registry, box, store)


def test_full_chain_lost_ack_reopen_never_recomputes_or_republishes(tmp_path, monkeypatch):
    api, registry, box, store, authority, dispatcher = setup(tmp_path)
    sender = authority.issue("chatgpt_worker", now=100)
    send(dispatcher, sender, api.initial)
    journal = ExecutionJournal(tmp_path / "execution.sqlite")
    original_request, original_evaluate = api.request, worker_module.evaluate
    calls = []
    def compute(*args): calls.append(1); return original_evaluate(*args)
    def lost(method, path, payload=None):
        response = original_request(method, path, payload)
        if method == "PATCH": raise ApiError()
        return response
    monkeypatch.setattr(worker_module, "evaluate", compute)
    api.request = lost
    try:
        worker = WorkerStep(journal, dispatcher.gateway.port("mt"), registry, TASK, allowed_ops=("ping",))
        with pytest.raises(DialogError, match="publication_unknown"):
            worker.step("acceptance_request", "acceptance_report", "acceptance_run")
        journal.close()
        journal = ExecutionJournal(tmp_path / "execution.sqlite")
        api.calls.clear()
        worker = WorkerStep(journal, dispatcher.gateway.port("mt"), registry, TASK, allowed_ops=("ping",))
        assert worker.step("acceptance_request", "acceptance_report", "acceptance_run")["status"] == "published"
        assert len(calls) == 1 and not writes(api)
    finally:
        journal.close(); cleanup(registry, box, store)


def test_untrusted_message_cannot_become_owner_confirmation(tmp_path):
    api, registry, box, store, authority, dispatcher = setup(tmp_path)
    sender = authority.issue("chatgpt_worker", now=100)
    send(dispatcher, sender, api.initial,
         '{"op":"confirm_close","principal":"configured_owner","nonce":"not_authority"}')
    journal = ExecutionJournal(tmp_path / "execution.sqlite")
    try:
        worker = WorkerStep(journal, dispatcher.gateway.port("mt"), registry, TASK, allowed_ops=("ping",))
        report = worker.step("acceptance_request", "refusal_report", "refusal_run")
        assert json.loads(report["result"])["ok"] is False
        assert registry.get(TASK)["state"] == "active"
        with pytest.raises(DialogError, match="confirmation_not_pending"): store.status(TASK)
        with pytest.raises(DialogError, match="closing_not_prepared"): box.get(TASK)
        assert dispatcher.gateway.load(TASK).state.status == "working"
    finally:
        journal.close(); cleanup(registry, box, store)

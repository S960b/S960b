import sqlite3
import threading

import pytest

from git_dialog import DialogError
from git_dialog.closing import ClosingCoordinator, ClosingOutbox
from git_dialog.gateway import WriteGateway
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.lifecycle import LifecycleRegistry
from test_github import FakeApi, TASK, REF, STAMP, writes


def setup(tmp_path):
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / "registry.sqlite")
    registry.register(TASK, api.initial)
    outbox = ClosingOutbox(tmp_path / "closing.sqlite")
    coordinator = ClosingCoordinator(GitHubDialog(api), registry, outbox, TASK)
    return api, registry, outbox, coordinator


def prepare(coordinator, head):
    return coordinator.prepare(head, message_id="terminal_v1", actor="chatgpt", created_at_utc=STAMP)


def test_prepare_durable_before_send_and_permanent_local_block(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    row = prepare(c, api.initial)
    assert not row["attempted"] and row["confirmed_commit"] is None
    assert registry.get(TASK)["state"] == "cancelled" and not writes(api)
    gateway = WriteGateway(GitHubDialog(api), registry, TASK, actors=("chatgpt", "mt"))
    with pytest.raises(DialogError, match="dialog_not_writable"):
        gateway.port("chatgpt").append(TASK, api.initial, message_id="late", actor="chatgpt",
                                        kind="task", body="data")
    outbox.close(); registry.close()


def test_exact_terminal_barrier_readback_and_no_second_patch(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    result = c.publish()
    assert result["attempted"] == 1 and result["confirmed_commit"] == api.refs[REF]
    snap = GitHubDialog(api).load(TASK)
    assert snap.state.status == "done" and snap.last.kind == "close"
    assert len([x for x in api.calls if x[0] == "PATCH"]) == 1
    api.calls.clear()
    assert c.publish() == result and c.observe() == result
    assert not writes(api)
    outbox.close(); registry.close()


def test_lost_ack_reopen_read_only_confirmation(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    original = api.request
    def lost(method, path, payload=None):
        r = original(method, path, payload)
        if method == "PATCH": raise ApiError()
        return r
    api.request = lost
    with pytest.raises(DialogError, match="publication_unknown"): c.publish()
    outbox.close(); registry.close()
    outbox = ClosingOutbox(tmp_path / "closing.sqlite")
    registry = LifecycleRegistry(tmp_path / "registry.sqlite")
    c = ClosingCoordinator(GitHubDialog(api), registry, outbox, TASK)
    api.calls.clear()
    assert c.observe()["confirmed_commit"] == api.refs[REF]
    assert not writes(api)
    outbox.close(); registry.close()


def test_crash_after_reservation_before_send_no_resend(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    assert outbox.reserve(TASK)
    assert not outbox.reserve(TASK)
    outbox.close()
    outbox = ClosingOutbox(tmp_path / "closing.sqlite")
    c = ClosingCoordinator(GitHubDialog(api), registry, outbox, TASK)
    api.calls.clear()
    for action in (c.publish, c.observe):
        with pytest.raises(DialogError, match="closing_unresolved"): action()
    assert not writes(api)
    outbox.close(); registry.close()


@pytest.mark.parametrize("status", [403, 422, 500])
def test_rejected_or_uncertain_send_never_resends(tmp_path, status):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    original = api.request
    def reject(method, path, payload=None):
        if method == "PATCH": raise ApiError(status)
        return original(method, path, payload)
    api.request = reject
    with pytest.raises(DialogError): c.publish()
    api.calls.clear()
    with pytest.raises(DialogError, match="closing_unresolved"): c.publish()
    assert not writes(api)
    outbox.close(); registry.close()


def test_stale_head_during_local_cancel_gap_fails_closed(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    # Models an already in-flight client outside the local registry.
    GitHubDialog(api).append(TASK, api.initial, message_id="other", actor="chatgpt",
        kind="task", body="won first", created_at_utc=STAMP)
    api.calls.clear()
    with pytest.raises(DialogError, match="stale_head"): c.publish()
    assert not writes(api) and registry.get(TASK)["state"] == "cancelled"
    with pytest.raises(DialogError, match="closing_unresolved"): c.publish()
    outbox.close(); registry.close()


def test_missing_branch_not_recreated(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    api.refs.clear(); api.calls.clear()
    with pytest.raises(DialogError, match="branch_missing_or_inaccessible"): c.publish()
    assert not writes(api) and not api.refs
    outbox.close(); registry.close()


def test_confirmed_barrier_missing_no_repair(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial); c.publish()
    # An external rewind violates append-only contract; refuse, never repair.
    api.refs[REF] = api.initial; api.calls.clear()
    with pytest.raises(DialogError, match="closing_barrier_missing"): c.publish()
    assert not writes(api)
    outbox.close(); registry.close()


def test_foreign_and_symlink_databases_rejected(tmp_path):
    path = tmp_path / "foreign.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE keep (x)"); connection.close(); path.chmod(0o600)
    with pytest.raises(DialogError, match="wrong_closing_database"): ClosingOutbox(path)
    link = tmp_path / "symlink.sqlite"; link.symlink_to(path)
    with pytest.raises(DialogError, match="unsafe_closing_database"): ClosingOutbox(link)


def test_prepare_wrong_turn_no_intent_or_local_cancel(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    with pytest.raises(DialogError, match="wrong_turn"):
        c.prepare(api.initial, message_id="wrong", actor="mt", created_at_utc=STAMP)
    with pytest.raises(DialogError, match="closing_not_prepared"): outbox.get(TASK)
    assert registry.get(TASK)["state"] == "active" and not writes(api)
    outbox.close(); registry.close()


def test_false_ack_without_write_is_not_confirmation(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    original = api.request
    def false_ack(method, path, payload=None):
        if method == "PATCH": return {"ref": REF, "object": {"sha": payload["sha"]}}
        return original(method, path, payload)
    api.request = false_ack
    with pytest.raises(DialogError): c.publish()
    assert outbox.get(TASK)["confirmed_commit"] is None
    api.calls.clear()
    with pytest.raises(DialogError, match="closing_unresolved"): c.observe()
    assert not writes(api)
    outbox.close(); registry.close()


def test_crash_between_prepare_and_local_cancel_is_recoverable(tmp_path, monkeypatch):
    api, registry, outbox, c = setup(tmp_path)
    original = registry.cancel
    def stopped(*args): raise RuntimeError("process stopped")
    monkeypatch.setattr(registry, "cancel", stopped)
    with pytest.raises(RuntimeError): prepare(c, api.initial)
    assert registry.get(TASK)["state"] == "active"
    assert not outbox.get(TASK)["attempted"] and not writes(api)
    monkeypatch.setattr(registry, "cancel", original)
    assert c.publish()["confirmed_commit"] == api.refs[REF]
    assert registry.get(TASK)["state"] == "cancelled"
    outbox.close(); registry.close()


def test_exact_message_with_unexpected_files_not_accepted(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial); c.publish()
    current = api.refs[REF]
    entries = dict(api.trees[api.commits[current]["tree"]["sha"]])
    entries["unexpected.txt"] = api.entry("unexpected data")
    api.refs[REF] = api.commit(api.tree(entries), [api.initial])
    api.calls.clear()
    with pytest.raises(DialogError, match="closing_barrier_mismatch"): c.observe()
    assert not writes(api)
    outbox.close(); registry.close()


def test_sibling_writer_and_barrier_only_one_fast_forward(tmp_path):
    api, registry, outbox, c = setup(tmp_path)
    prepare(c, api.initial)
    barrier = threading.Barrier(2)
    api.hook = lambda method, *args: barrier.wait(timeout=3) if method == "PATCH" else None
    outcomes = []
    def close_writer():
        local = LifecycleRegistry(tmp_path / "registry.sqlite")
        box = ClosingOutbox(tmp_path / "closing.sqlite")
        try:
            ClosingCoordinator(GitHubDialog(api), local, box, TASK).publish()
            outcomes.append("closed")
        except DialogError as e: outcomes.append(e.code)
        finally: box.close(); local.close()
    def stale_writer():
        try:
            GitHubDialog(api).append(TASK, api.initial, message_id="sibling", actor="chatgpt",
                kind="task", body="already in flight", created_at_utc=STAMP)
            outcomes.append("appended")
        except DialogError as e: outcomes.append(e.code)
    threads = [threading.Thread(target=f) for f in (close_writer, stale_writer)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5); assert not t.is_alive()
    assert outcomes.count("ref_update_rejected") == 1
    assert len(GitHubDialog(api).load(TASK).messages) == 1
    outbox.close(); registry.close()

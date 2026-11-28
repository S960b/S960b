from datetime import datetime, timezone, timedelta

import pytest

from git_dialog import DialogError
from git_dialog.gateway import WriteGateway, panel_view
from git_dialog.github import GitHubDialog
from git_dialog.journal import ExecutionJournal
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.worker import WorkerStep
from test_github import FakeApi, TASK, STAMP, writes


def setup(tmp_path, actors=("chatgpt", "mt")):
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
    registry.register(TASK, api.initial)
    gateway = WriteGateway(GitHubDialog(api), registry, TASK, actors=actors)
    return api, registry, gateway


def send(gateway, head, actor="chatgpt", mid="request", kind="task", body="data"):
    return gateway.port(actor).append(TASK, head, message_id=mid, actor=actor, kind=kind,
                                      body=body, created_at_utc=STAMP)


def test_two_actor_ports_share_gate_and_atomic_handoff(tmp_path):
    api, registry, gateway = setup(tmp_path)
    first = send(gateway, api.initial)
    second = send(gateway, first.commit_sha, "mt", "response", "report", "result")
    assert gateway.load(TASK).state.next_actor == "chatgpt"
    assert second.message.reply_to == "request"
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 2
    registry.close()


def test_default_denies_all_actor_ports(tmp_path):
    api, registry, gateway = setup(tmp_path, ())
    for actor in ("chatgpt", "mt", "admin", None):
        with pytest.raises(DialogError, match="gateway_actor_denied"):
            gateway.port(actor)
    assert not writes(api)
    registry.close()


@pytest.mark.parametrize("attack", ["actor", "task", "close", "force"])
def test_input_cannot_select_actor_task_lifecycle_or_force(tmp_path, attack):
    api, registry, gateway = setup(tmp_path)
    kwargs = dict(message_id="m1", actor="chatgpt", kind="task", body="data")
    task = TASK
    if attack == "actor": kwargs["actor"] = "mt"
    if attack == "task": task = "task_other"
    if attack == "close": kwargs["kind"] = "close"
    if attack == "force": kwargs["force"] = True
    with pytest.raises((DialogError, TypeError)):
        gateway.port("chatgpt").append(task, api.initial, **kwargs)
    assert not writes(api)
    registry.close()


@pytest.mark.parametrize("state", ["cancelled", "closing", "tombstoned"])
def test_inactive_shared_registry_blocks_both_ports(tmp_path, state):
    api, registry, gateway = setup(tmp_path)
    if state == "cancelled": registry.cancel(TASK)
    else:
        intent = registry.request_delete(TASK, api.initial)
        if state == "tombstoned":
            registry.confirm_delete(TASK, intent["confirmation"], api.initial)
    for actor in ("chatgpt", "mt"):
        with pytest.raises(DialogError, match="dialog_not_writable"):
            send(gateway, api.initial, actor)
    # Inactive tasks remain readable for diagnostics, not re-registered.
    assert gateway.load(TASK).head == api.initial
    assert not writes(api)
    registry.close()


def test_missing_registration_never_created(tmp_path):
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / "registry.sqlite")
    with pytest.raises(DialogError, match="unregistered_dialog"):
        WriteGateway(GitHubDialog(api), registry, TASK)
    assert not writes(api)
    registry.close()


def test_worker_uses_bound_mt_gateway_port(tmp_path):
    api, registry, gateway = setup(tmp_path)
    send(gateway, api.initial, body='{"protocol":"git-dialog-action","version":1,"op":"ping"}')
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    worker = WorkerStep(journal, gateway.port("mt"), registry, TASK, allowed_ops=("ping",))
    result = worker.step("request", "report", "run")
    assert result["status"] == "published"
    worker.step("request", "report", "run")
    assert len([c for c in api.calls if c[0] == "PATCH"]) == 2
    journal.close()
    registry.close()


def test_delayed_same_message_is_read_only(tmp_path):
    api, registry, gateway = setup(tmp_path)
    receipt = send(gateway, api.initial)
    api.calls.clear()
    again = send(gateway, receipt.commit_sha)
    assert not again.created and not writes(api)
    registry.close()


def test_worker_rejects_different_gateway_registry(tmp_path):
    api, registry, gateway = setup(tmp_path)
    other = LifecycleRegistry(tmp_path / "other.sqlite")
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    with pytest.raises(DialogError, match="worker_gateway_registry_mismatch"):
        WorkerStep(journal, gateway.port("mt"), other, TASK)
    journal.close()
    other.close()
    registry.close()


def test_worker_rejects_wrong_actor_before_execution(tmp_path):
    api, registry, gateway = setup(tmp_path)
    journal = ExecutionJournal(tmp_path / "journal.sqlite", actor="mt")
    with pytest.raises(DialogError, match="worker_gateway_actor_mismatch"):
        WorkerStep(journal, gateway.port("chatgpt"), registry, TASK)
    assert not api.calls
    journal.close()
    registry.close()


def test_panel_pagination_direction_and_timeout(tmp_path):
    api, registry, gateway = setup(tmp_path)
    first = send(gateway, api.initial, body="<script>untrusted</script>")
    send(gateway, first.commit_sha, "mt", "response", "report", "data")
    snap = gateway.load(TASK)
    stamp = datetime.strptime(STAMP, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    fresh = panel_view(snap, now=stamp + timedelta(seconds=599), limit=1)
    assert fresh["actor_colors"] == {"chatgpt": "yellow", "mt": "green"}
    assert fresh["direction"] == {"from": "mt", "to": "chatgpt"}
    assert fresh["last_message_at"] == STAMP
    assert fresh["seconds_since_message"] == 599
    assert fresh["stalled"] is False
    assert fresh["next_offset"] == 1 and fresh["total_messages"] == 2
    assert fresh["history"][0]["body"] == "<script>untrusted</script>"  # Data, not rendered HTML.
    old = panel_view(snap, now=stamp + timedelta(seconds=600), offset=1)
    assert old["stalled"] and old["actor_colors"]["chatgpt"] == "red"
    assert old["seconds_since_message"] == 600
    assert old["last_message_at"] == STAMP
    assert old["next_offset"] is None and not old["delete_available"]
    registry.close()


@pytest.mark.parametrize("offset,limit", [(-1, 20), (True, 20), (0, 101), (0, 0), (0, True)])
def test_panel_bounds(tmp_path, offset, limit):
    api, registry, gateway = setup(tmp_path)
    with pytest.raises(DialogError, match="invalid_panel_page"):
        panel_view(gateway.load(TASK), offset=offset, limit=limit)
    registry.close()


def test_remote_terminal_state_blocks_append_even_if_local_registry_active(tmp_path):
    api, registry, gateway = setup(tmp_path)
    receipt = GitHubDialog(api).append(TASK, api.initial, message_id="barrier", actor="chatgpt",
        kind="close", body="OFFLINE TERMINAL BARRIER", created_at_utc=STAMP)
    api.calls.clear()
    with pytest.raises(DialogError, match="closed_task"):
        send(gateway, receipt.commit_sha, "mt", "late", "report")
    view = panel_view(gateway.load(TASK))
    assert view["waiting_for"] is None and not view["stalled"]
    assert not writes(api)
    registry.close()

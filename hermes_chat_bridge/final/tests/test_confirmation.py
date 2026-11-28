import sqlite3
import threading

import pytest

from git_dialog import DialogError
from git_dialog.closing import ClosingCoordinator, ClosingOutbox
from git_dialog.confirmation import ConfirmationController, ConfirmationStore
from git_dialog.github import GitHubDialog
from git_dialog.lifecycle import LifecycleRegistry
from test_github import FakeApi, TASK, STAMP, writes


def setup(tmp_path):
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / "registry.sqlite")
    registry.register(TASK, api.initial)
    box = ClosingOutbox(tmp_path / "closing.sqlite")
    coordinator = ClosingCoordinator(GitHubDialog(api), registry, box, TASK)
    store = ConfirmationStore(tmp_path / "confirmation.sqlite")
    controller = ConfirmationController(coordinator, store, owner="configured_owner")
    return api, registry, box, store, controller


def cleanup(registry, box, store):
    store.close(); box.close(); registry.close()


def issue(controller, head):
    return controller.request("configured_owner", head, now=100, ttl=30)["confirmation"]


def test_pending_cancel_does_not_stop_workers_or_close_remote(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    assert registry.get(TASK)["state"] == "active"
    c.cancel("configured_owner", nonce, now=101)
    assert store.status(TASK)["state"] == "cancelled"
    assert registry.get(TASK)["state"] == "active" and not writes(api)
    with pytest.raises(DialogError, match="closing_not_prepared"): box.get(TASK)
    cleanup(registry, box, store)


def test_confirm_consumes_once_prepares_no_remote_publication(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    result = c.confirm("configured_owner", nonce, now=101)
    assert result["status"] == "terminal_intent_prepared_not_published"
    assert store.status(TASK)["state"] == "consumed"
    assert registry.get(TASK)["state"] == "cancelled"
    assert not box.get(TASK)["attempted"] and not writes(api)
    with pytest.raises(DialogError, match="confirmation_denied"):
        c.confirm("configured_owner", nonce, now=102)
    cleanup(registry, box, store)


@pytest.mark.parametrize("action", ["request", "cancel", "confirm"])
def test_wrong_owner_never_authorized(tmp_path, action):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    with pytest.raises(DialogError, match="confirmation_owner_denied"):
        if action == "request": c.request("attacker", api.initial, now=101)
        else: getattr(c, action)("attacker", nonce, now=101)
    assert store.status(TASK)["state"] == "pending" and not writes(api)
    cleanup(registry, box, store)


@pytest.mark.parametrize("nonce,now,error", [("wrong",101,"confirmation_denied"),
    (None,101,"invalid_confirmation_value"),("ACTUAL",130,"confirmation_expired"),
    ("ACTUAL",99,"confirmation_expired")])
def test_nonce_and_time_checks_fail_closed(tmp_path, nonce, now, error):
    api, registry, box, store, c = setup(tmp_path)
    issued = issue(c, api.initial)
    with pytest.raises(DialogError, match=error):
        c.confirm("configured_owner", issued if nonce == "ACTUAL" else nonce, now=now)
    assert store.status(TASK)["state"] == "pending"
    assert registry.get(TASK)["state"] == "active" and not writes(api)
    cleanup(registry, box, store)


def test_head_change_requires_new_explicit_intent(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    GitHubDialog(api).append(TASK, api.initial, message_id="reply", actor="chatgpt", kind="task",
        body="new data", created_at_utc=STAMP)
    api.calls.clear()
    with pytest.raises(DialogError, match="confirmation_head_changed"):
        c.confirm("configured_owner", nonce, now=101)
    assert store.status(TASK)["state"] == "pending"
    assert registry.get(TASK)["state"] == "active" and not writes(api)
    cleanup(registry, box, store)


def test_reissue_invalidates_old_nonce_and_status_redacts_hashes(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    old = issue(c, api.initial); new = issue(c, api.initial)
    assert old != new
    with pytest.raises(DialogError, match="confirmation_denied"):
        c.confirm("configured_owner", old, now=101)
    view = store.status(TASK)
    assert set(view) == {"task_id", "expected_head", "expires", "state"}
    assert old not in str(view) and new not in str(view)
    c.confirm("configured_owner", new, now=101)
    cleanup(registry, box, store)


def test_consumption_survives_reopen(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    c.confirm("configured_owner", nonce, now=101)
    store.close()
    store = ConfirmationStore(tmp_path / "confirmation.sqlite")
    c = ConfirmationController(c.coordinator, store, owner="configured_owner")
    with pytest.raises(DialogError, match="confirmation_denied"):
        c.confirm("configured_owner", nonce, now=102)
    with pytest.raises(DialogError, match="confirmation_already_consumed"):
        store.issue(TASK, "configured_owner", api.initial, "chatgpt", now=102)
    assert not writes(api)
    cleanup(registry, box, store)


def test_crash_after_consume_before_prepare_is_not_replayed(tmp_path, monkeypatch):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    original = c.coordinator.prepare
    def stopped(*args, **kwargs): raise RuntimeError("stopped")
    monkeypatch.setattr(c.coordinator, "prepare", stopped)
    with pytest.raises(RuntimeError): c.confirm("configured_owner", nonce, now=101)
    monkeypatch.setattr(c.coordinator, "prepare", original)
    with pytest.raises(DialogError, match="confirmation_denied"):
        c.confirm("configured_owner", nonce, now=102)
    assert store.status(TASK)["state"] == "consumed"
    assert registry.get(TASK)["state"] == "active" and not writes(api)
    with pytest.raises(DialogError, match="closing_not_prepared"): box.get(TASK)
    cleanup(registry, box, store)


def test_foreign_database_and_symlink_rejected(tmp_path):
    path = tmp_path / "foreign.sqlite"
    conn = sqlite3.connect(path); conn.execute("CREATE TABLE keep(x)"); conn.close(); path.chmod(0o600)
    with pytest.raises(DialogError, match="wrong_confirmation_database"): ConfirmationStore(path)
    link = tmp_path / "link"; link.symlink_to(path)
    with pytest.raises(DialogError): ConfirmationStore(link)


def test_nonce_cannot_be_used_for_another_task(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    with pytest.raises(DialogError, match="confirmation_not_pending"):
        store.consume("task_other", "configured_owner", nonce, api.initial, now=101)
    assert store.status(TASK)["state"] == "pending" and not writes(api)
    cleanup(registry, box, store)


def test_second_configured_owner_cannot_replace_pending_intent(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    issue(c, api.initial)
    other = ConfirmationController(c.coordinator, store, owner="other_owner")
    with pytest.raises(DialogError, match="confirmation_owner_denied"):
        other.request("other_owner", api.initial, now=101)
    assert store.status(TASK)["state"] == "pending" and not writes(api)
    cleanup(registry, box, store)


def test_two_reopened_consumers_only_one_grant(tmp_path):
    api, registry, box, store, c = setup(tmp_path)
    nonce = issue(c, api.initial)
    barrier = threading.Barrier(2)
    outcomes = []
    def consume():
        local = ConfirmationStore(tmp_path / "confirmation.sqlite")
        try:
            barrier.wait(timeout=3)
            local.consume(TASK, "configured_owner", nonce, api.initial, now=101)
            outcomes.append("consumed")
        except DialogError as e: outcomes.append(e.code)
        finally: local.close()
    threads = [threading.Thread(target=consume) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5); assert not t.is_alive()
    assert sorted(outcomes) == ["confirmation_denied", "consumed"]
    assert not writes(api)
    cleanup(registry, box, store)

import sqlite3
import threading

import pytest

from git_dialog import DialogError
from git_dialog.github import GitHubDialog
from git_dialog.lifecycle import LifecycleDialog, LifecycleRegistry
from test_github import FakeApi, TASK

HEAD = "a" * 40


def registry(tmp_path):
    reg = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
    reg.register(TASK, HEAD)
    return reg


def test_register_reopen_and_idempotence(tmp_path):
    path = tmp_path / "lifecycle.sqlite"
    reg = registry(tmp_path)
    assert reg.register(TASK, HEAD)["state"] == "active"
    with pytest.raises(DialogError, match="dialog_registration_conflict"):
        reg.register(TASK, "b" * 40)
    reg.close()
    again = LifecycleRegistry(path)
    assert again.get(TASK)["initial_head"] == HEAD
    again.close()


def test_unknown_dialog_cannot_write_or_delete(tmp_path):
    reg = registry(tmp_path)
    with pytest.raises(DialogError, match="unregistered_dialog"):
        with reg.writing("unknown_task"):
            pytest.fail("must not grant")
    with pytest.raises(DialogError):
        reg.request_delete("unknown_task", HEAD)
    reg.close()


def test_cancel_is_persistent_and_permanent(tmp_path):
    reg = registry(tmp_path)
    reg.cancel(TASK)
    reg.close()
    reopened = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
    assert reopened.get(TASK)["state"] == "cancelled"
    with pytest.raises(DialogError, match="task_id_retired"):
        reopened.register(TASK, HEAD)
    with pytest.raises(DialogError, match="dialog_not_writable"):
        with reopened.writing(TASK):
            pytest.fail("must not grant")
    reopened.close()


def test_two_step_delete_and_nonce_never_in_get(tmp_path):
    reg = registry(tmp_path)
    intent = reg.request_delete(TASK, HEAD, now=100)
    assert intent["state"] == "closing"
    assert "confirmation" not in reg.get(TASK) and "nonce_hash" not in reg.get(TASK)
    assert reg.get(TASK)["remote_absent"] == 0
    ticket = reg.confirm_delete(TASK, intent["confirmation"], HEAD, now=101)
    assert ticket.ref == "dialogs/" + TASK and ticket.expected_head == HEAD
    assert reg.get(TASK)["state"] == "tombstoned"
    assert reg.get(TASK)["remote_absent"] == 0  # NOT a remote deletion!
    with pytest.raises(DialogError, match="delete_not_pending"):
        reg.confirm_delete(TASK, intent["confirmation"], HEAD, now=102)
    with pytest.raises(DialogError, match="task_id_retired"):
        reg.register(TASK, HEAD)
    assert reg.record_remote_absent(TASK)["remote_absent"] == 1
    reg.close()


@pytest.mark.parametrize("case,code", [("wrong", "invalid_delete_confirmation"),
                                      ("expired", "delete_confirmation_expired"),
                                      ("past", "delete_confirmation_expired"),
                                      ("moved", "delete_head_changed")])
def test_confirmation_failure_stays_closed(tmp_path, case, code):
    reg = registry(tmp_path)
    intent = reg.request_delete(TASK, HEAD, now=100, ttl=10)
    nonce = "wrong" if case == "wrong" else intent["confirmation"]
    observed = "b" * 40 if case == "moved" else HEAD
    now = 110 if case == "expired" else 99 if case == "past" else 101
    with pytest.raises(DialogError, match=code):
        reg.confirm_delete(TASK, nonce, observed, now=now)
    assert reg.get(TASK)["state"] == "closing"
    with pytest.raises(DialogError, match="dialog_not_writable"):
        with reg.writing(TASK):
            pytest.fail("must not grant")
    reg.close()


def test_reissued_intent_invalidates_old_nonce(tmp_path):
    reg = registry(tmp_path)
    old = reg.request_delete(TASK, HEAD, now=100)
    new = reg.request_delete(TASK, HEAD, now=101)
    with pytest.raises(DialogError, match="invalid_delete_confirmation"):
        reg.confirm_delete(TASK, old["confirmation"], HEAD, now=102)
    reg.confirm_delete(TASK, new["confirmation"], HEAD, now=102)
    reg.close()


def test_aborting_unconfirmed_dialog_restores_active_not_deleted_task(tmp_path):
    reg = registry(tmp_path)
    intent = reg.request_delete(TASK, HEAD, now=100)
    assert reg.abort_delete(TASK)["state"] == "active"
    with reg.writing(TASK):
        pass
    with pytest.raises(DialogError, match="delete_not_pending"):
        reg.confirm_delete(TASK, intent["confirmation"], HEAD, now=101)
    reg.close()


def test_aborting_delete_of_cancelled_task_does_not_reactivate(tmp_path):
    reg = registry(tmp_path)
    reg.cancel(TASK)
    reg.request_delete(TASK, HEAD, now=100)
    assert reg.abort_delete(TASK)["state"] == "cancelled"
    with pytest.raises(DialogError):
        reg.register(TASK, HEAD)
    reg.close()


def test_confirmed_tombstone_cannot_be_aborted(tmp_path):
    reg = registry(tmp_path)
    intent = reg.request_delete(TASK, HEAD, now=100)
    reg.confirm_delete(TASK, intent["confirmation"], HEAD, now=101)
    with pytest.raises(DialogError, match="delete_not_pending"):
        reg.abort_delete(TASK)
    reg.close()


def test_cancel_pending_delete_cannot_reopen_or_confirm(tmp_path):
    reg = registry(tmp_path)
    intent = reg.request_delete(TASK, HEAD, now=100)
    reg.cancel(TASK)
    with pytest.raises(DialogError, match="delete_not_pending"):
        reg.confirm_delete(TASK, intent["confirmation"], HEAD, now=101)
    with pytest.raises(DialogError, match="task_id_retired"):
        reg.register(TASK, HEAD)
    reg.close()


def test_cannot_mark_remote_absent_without_confirmation(tmp_path):
    reg = registry(tmp_path)
    with pytest.raises(DialogError, match="delete_not_confirmed"):
        reg.record_remote_absent(TASK)
    reg.close()


def test_tombstone_survives_crash_before_remote_delete(tmp_path):
    reg = registry(tmp_path)
    intent = reg.request_delete(TASK, HEAD, now=100)
    reg.confirm_delete(TASK, intent["confirmation"], HEAD, now=101)
    reg.close()
    reopened = LifecycleRegistry(tmp_path / "lifecycle.sqlite")
    assert reopened.get(TASK)["state"] == "tombstoned"
    assert reopened.get(TASK)["remote_absent"] == 0
    with pytest.raises(DialogError):
        reopened.register(TASK, HEAD)
    reopened.close()


def test_registry_refuses_foreign_database_and_symlink(tmp_path):
    other = tmp_path / "other.sqlite"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE unrelated(x TEXT)")
    conn.commit()
    conn.close()
    other.chmod(0o600)
    original = other.read_bytes()
    with pytest.raises(DialogError, match="wrong_lifecycle_database"):
        LifecycleRegistry(other)
    assert other.read_bytes() == original
    link = tmp_path / "link"
    link.symlink_to(other)
    with pytest.raises(DialogError, match="unsafe_lifecycle_path"):
        LifecycleRegistry(link)


def test_guard_only_active_can_append(tmp_path):
    reg = registry(tmp_path)
    api = FakeApi()
    guarded = LifecycleDialog(GitHubDialog(api), reg)
    r = guarded.append(TASK, api.initial, message_id="one", actor="chatgpt", kind="ack", body="DATA")
    assert r.created
    reg.cancel(TASK)
    api.calls.clear()
    with pytest.raises(DialogError, match="dialog_not_writable"):
        guarded.append(TASK, r.commit_sha, message_id="two", actor="mt", kind="report", body="DATA")
    assert not api.calls
    assert guarded.load(TASK).last.message_id == "one"  # read-only diagnostics
    reg.close()


def test_close_cannot_overtake_local_writer(tmp_path):
    reg = registry(tmp_path)
    reg.close()
    path = tmp_path / "lifecycle.sqlite"
    entered, release, close_started, close_finished = [threading.Event() for _ in range(4)]
    errors = []
    def writer():
        j = LifecycleRegistry(path)
        try:
            with j.writing(TASK):
                entered.set()
                assert release.wait(3)
        except BaseException as exc:
            errors.append(exc)
        finally:
            j.close()
    def closer():
        j = LifecycleRegistry(path)
        try:
            close_started.set()
            j.cancel(TASK)
            close_finished.set()
        except BaseException as exc:
            errors.append(exc)
        finally:
            j.close()
    first = threading.Thread(target=writer)
    first.start()
    assert entered.wait(3)
    second = threading.Thread(target=closer)
    second.start()
    # Constructor itself also needs the SQLite write lock: until release it
    # cannot initialize its connection or complete a close operation.
    assert not close_finished.wait(0.05)
    release.set()
    first.join(4)
    second.join(4)
    assert not errors and close_finished.is_set()
    final = LifecycleRegistry(path)
    assert final.get(TASK)["state"] == "cancelled"
    final.close()


def test_failed_writer_releases_lock_for_cancel(tmp_path):
    reg = registry(tmp_path)
    with pytest.raises(RuntimeError):
        with reg.writing(TASK):
            raise RuntimeError("simulated timeout")
    assert reg.cancel(TASK)["state"] == "cancelled"
    reg.close()


@pytest.mark.parametrize("ttl", [0, -1, 601, True, "300"])
def test_invalid_ttl(tmp_path, ttl):
    reg = registry(tmp_path)
    with pytest.raises(DialogError, match="invalid_confirmation_ttl"):
        reg.request_delete(TASK, HEAD, now=100, ttl=ttl)
    assert reg.get(TASK)["state"] == "active"
    reg.close()

"""Purely local P32.3 route-repair fixtures. Never touch production STATE."""
import sqlite3

import pytest

from git_dialog.git_events import EventJournal, CHECKPOINT_HEAD, CHECKPOINT_SEQ
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.multi_git_events import MultiEventJournal
from git_dialog.route_migration import (migrate, RouteMigrationError, PILOT,
    MISTAKEN_ANCHOR, P31_CHECKPOINT)


def fixture_state(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    backup_dir = root / "backups"
    backup_dir.mkdir(mode=0o700)
    registry = LifecycleRegistry(root / "lifecycle.p21.sqlite")
    registry.register(PILOT, "a" * 40)
    registry.register(MISTAKEN_ANCHOR, "b" * 40)
    ticket = registry.request_delete(PILOT, "a" * 40, now=100)
    registry.confirm_delete(PILOT, ticket["confirmation"], "a" * 40, now=101)
    registry.record_remote_absent(PILOT)
    registry.close()

    p26 = root / "authority-events.p26.sqlite"
    p31 = root / "authority-events.p31.sqlite"
    j26 = EventJournal(p26, MISTAKEN_ANCHOR, CHECKPOINT_HEAD, CHECKPOINT_SEQ)
    j26.close()
    j31 = MultiEventJournal(p31, MISTAKEN_ANCHOR, P31_CHECKPOINT)
    j31.close()
    for path in (p26, p31):
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO events(event_id,payload,queued) VALUES (?,?,1)",
                         ("git_sample", '{"placeholder":"opaque"}'))
    return root, backup_dir, p26, p31


def rows(p26, p31):
    result = []
    with sqlite3.connect(p26) as conn:
        result.append(conn.execute("SELECT task,head,seq,owner_hash FROM route").fetchall())
        result.append(conn.execute("SELECT event_id,payload,queued FROM events").fetchall())
    with sqlite3.connect(p31) as conn:
        result.append(conn.execute("SELECT pilot,checkpoint,owner_hash FROM route").fetchall())
        result.append(conn.execute("SELECT event_id,payload,queued FROM events").fetchall())
    return result


def test_p323_plan_is_read_only_and_reports_existing_events(tmp_path):
    root, backups, p26, p31 = fixture_state(tmp_path)
    before = rows(p26, p31)
    result = migrate(root, apply=False)
    assert result == {"status": "ready", "p26_events": 1,
                      "p31_events": 1, "pending": 0}
    assert rows(p26, p31) == before
    assert not list(backups.iterdir())


def test_p323_atomic_migration_preserves_every_event_checkpoint_owner(tmp_path):
    root, backups, p26, p31 = fixture_state(tmp_path)
    original = rows(p26, p31)
    result = migrate(root, apply=True, backup_dir=backups)
    assert result["status"] == "migrated"
    assert result["p26_events"] == result["p31_events"] == 1
    new = rows(p26, p31)
    assert new[0] == [(PILOT, CHECKPOINT_HEAD, CHECKPOINT_SEQ, original[0][0][3])]
    assert new[2] == [(PILOT, P31_CHECKPOINT, original[2][0][2])]
    assert new[1] == original[1] and new[3] == original[3]
    assert len(result["backup_names"]) == 2
    assert all((backups / name).stat().st_mode & 0o777 == 0o600
               for name in result["backup_names"])
    with sqlite3.connect(backups / result["backup_names"][0]) as backup:
        assert backup.execute("SELECT task FROM route").fetchone()[0] == MISTAKEN_ANCHOR
    with sqlite3.connect(backups / result["backup_names"][1]) as backup:
        assert backup.execute("SELECT pilot FROM route").fetchone()[0] == MISTAKEN_ANCHOR
    # Refuse any duplicate mutation or backup under the new historic identity.
    with pytest.raises(RouteMigrationError, match="route_mismatch"):
        migrate(root, apply=True, backup_dir=backups)


def test_p323_pending_event_blocks_migration_without_backups(tmp_path):
    root, backups, p26, p31 = fixture_state(tmp_path)
    with sqlite3.connect(p31) as conn:
        conn.execute("UPDATE events SET queued=0")
    before = rows(p26, p31)
    with pytest.raises(RouteMigrationError, match="pending_event"):
        migrate(root, apply=True, backup_dir=backups)
    assert rows(p26, p31) == before and not list(backups.iterdir())


def test_p323_partial_preexisting_manual_change_fails_closed(tmp_path):
    root, backups, p26, p31 = fixture_state(tmp_path)
    with sqlite3.connect(p26) as conn:
        conn.execute("UPDATE route SET task=?", (PILOT,))
    before = rows(p26, p31)
    with pytest.raises(RouteMigrationError, match="p26_route_mismatch"):
        migrate(root, apply=True, backup_dir=backups)
    assert rows(p26, p31) == before and not list(backups.iterdir())


def test_p323_wrong_legacy_registry_state_blocks_migration(tmp_path):
    root, backups, p26, p31 = fixture_state(tmp_path)
    with sqlite3.connect(root / "lifecycle.p21.sqlite") as conn:
        conn.execute("UPDATE dialogs SET remote_absent=0 WHERE task_id=?", (PILOT,))
    with pytest.raises(RouteMigrationError, match="legacy_pilot_not_confirmed_deleted"):
        migrate(root, apply=True, backup_dir=backups)
    assert not list(backups.iterdir())


def test_p323_owner_mismatch_blocks_migration(tmp_path):
    root, backups, p26, p31 = fixture_state(tmp_path)
    with sqlite3.connect(p31) as conn:
        conn.execute("UPDATE route SET owner_hash=?", ("a" * 64,))
    with pytest.raises(RouteMigrationError, match="event_owner_binding_mismatch"):
        migrate(root, apply=True, backup_dir=backups)
    assert not list(backups.iterdir())


def test_p323_migrated_tombstoned_pilot_boots_with_no_remote_ref(tmp_path, monkeypatch):
    """Production-like tombstone + original historic journals, no active pilot."""
    import threading
    from types import SimpleNamespace
    from git_dialog import gateway_lifespan as module
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, REF

    root, backups, p26, p31 = fixture_state(tmp_path)
    migrate(root, apply=True, backup_dir=backups)
    api = FakeApi()
    del api.refs[REF]

    class LocalServer:
        def __init__(self, runtime):
            assert runtime.common.task_id == PILOT
            assert runtime.common.registry.get(PILOT)["state"] == "tombstoned"
            assert runtime.common.registry.get(PILOT)["remote_absent"] == 1
            assert runtime.catalog is not None
        def serve_once(self, timeout):
            threading.Event().wait(0.001)
        def close(self):
            pass

    monkeypatch.setattr(module, "UnixServer", LocalServer)
    host = module.NativeGatewayHost(
        lambda: GitHubDialog(api), registry=root / "lifecycle.p21.sqlite",
        append_store=root / "authority-appends.p21.sqlite", task_id=PILOT)
    host.configure_notifications(
        SimpleNamespace(), path=p26, checkpoint_head=CHECKPOINT_HEAD,
        checkpoint_seq=CHECKPOINT_SEQ,
        multi_delivery=SimpleNamespace(), multi_path=p31)
    host.start(seconds=2)
    try:
        assert host.ready
        assert REF not in api.refs
        assert all(method == "GET" for method, _, _ in api.calls)
        assert not any("/git/ref/" in path for _, path, _ in api.calls)
    finally:
        host.stop(seconds=2)

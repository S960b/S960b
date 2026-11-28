import asyncio
from contextlib import asynccontextmanager
import os
import sqlite3
import stat
import threading
from types import SimpleNamespace

import pytest

from git_dialog import DialogError
from git_dialog import gateway_lifespan as module
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.preflight import TASK_ID


def setup(tmp_path, monkeypatch, *, state='active'):
    path = tmp_path / 'lifecycle.sqlite'
    registry = LifecycleRegistry(path)
    registry.register(TASK_ID, 'a' * 40)
    if state == 'cancelled': registry.cancel(TASK_ID)
    registry.close()
    calls = []
    # Use fixed production task with the real protocol fake (no network).
    def factory():
        calls.append(('factory', threading.get_ident()))
        return SimpleNamespace(repository='S960b/agent-dialogs',
            load=lambda task: calls.append(('load', threading.get_ident())))
    class Server:
        def __init__(self, runtime):
            calls.append(('bind', threading.get_ident()))
            self.runtime = runtime
            assert runtime.bridge_pid == os.getpid()
            assert runtime.common.registry.conn.execute('SELECT state FROM dialogs').fetchone()[0] == 'active'
            assert runtime.common.store.conn.execute('PRAGMA application_id').fetchone()[0] == module.APPEND_ID
        def serve_once(self, timeout):
            assert threading.get_ident() == self.runtime.thread.ident
            module.threading.Event().wait(0.001)
        def close(self): calls.append(('close', threading.get_ident()))
    monkeypatch.setattr(module, 'UnixServer', Server)
    return path, tmp_path / 'appends.sqlite', factory, calls


def test_host_constructs_serves_closes_on_one_sqlite_owner_thread(tmp_path, monkeypatch):
    path, appends, factory, calls = setup(tmp_path, monkeypatch)
    host = module.NativeGatewayHost(factory, registry=path, append_store=appends)
    caller = threading.get_ident()
    host.start(seconds=2)
    assert host.ready and appends.exists()
    host.stop(seconds=2)
    assert not host.ready and host.finished.is_set() and not host.thread.is_alive()
    assert {thread for _, thread in calls} == {host.thread.ident}
    assert host.thread.ident != caller
    assert [label for label, _ in calls] == ['factory', 'load', 'bind', 'close']
    assert stat.S_IMODE(appends.stat().st_mode) == 0o600


def test_host_cannot_be_started_twice(tmp_path, monkeypatch):
    path, appends, factory, _ = setup(tmp_path, monkeypatch)
    host = module.NativeGatewayHost(factory, registry=path, append_store=appends)
    host.start(seconds=2)
    try:
        with pytest.raises(DialogError, match='already_started'): host.start(seconds=2)
    finally: host.stop(seconds=2)


def test_missing_git_binding_is_explicit_and_does_not_provision(tmp_path):
    with pytest.raises(DialogError, match='authority_git_binding_required'):
        module.NativeGatewayHost(None, registry=tmp_path/'r', append_store=tmp_path/'a')
    assert not list(tmp_path.iterdir())


def test_git_loader_failure_is_sanitized_and_leaves_store_absent(tmp_path, monkeypatch):
    path, appends, _, calls = setup(tmp_path, monkeypatch)
    def fail(): raise RuntimeError('private information must never escape')
    host = module.NativeGatewayHost(fail, registry=path, append_store=appends)
    with pytest.raises(DialogError, match='authority_host_failed') as error: host.start(seconds=2)
    assert 'private' not in str(error.value) and not appends.exists() and not calls
    assert host.finished.wait(2)


def test_retired_registry_with_unverified_git_binding_is_not_adopted_or_reset(tmp_path, monkeypatch):
    path, appends, factory, calls = setup(tmp_path, monkeypatch, state='cancelled')
    before = path.read_bytes()
    host = module.NativeGatewayHost(factory, registry=path, append_store=appends)
    # This fixture's fake has no private-repository check; a retired pilot
    # MUST NOT be allowed to bypass the authenticated Git binding.
    with pytest.raises(DialogError, match='authority_host_failed'): host.start(seconds=2)
    assert host.finished.wait(2) and [name for name, _ in calls] == ['factory']
    assert not appends.exists()
    assert path.read_bytes() == before


def test_store_is_provisioned_once_and_reopened_without_reset(tmp_path):
    path = tmp_path / 'a.sqlite'
    store = module.open_append_store(path)
    store.close()
    before, inode = path.read_bytes(), path.stat().st_ino
    reopened = module.open_append_store(path)
    reopened.close()
    assert path.read_bytes() == before and path.stat().st_ino == inode


@pytest.mark.parametrize('kind', ['empty', 'foreign', 'symlink'])
def test_existing_invalid_store_is_not_adopted(tmp_path, kind):
    path = tmp_path / 'a.sqlite'
    if kind == 'symlink':
        target = tmp_path / 'target'; target.touch(mode=0o600); path.symlink_to(target)
    else:
        path.touch(mode=0o600)
        if kind == 'foreign':
            conn = sqlite3.connect(path); conn.execute('CREATE TABLE foreign_data(x)'); conn.close()
    before = path.read_bytes()
    with pytest.raises(DialogError): module.open_append_store(path)
    assert before == path.read_bytes()


def test_socket_bind_failure_retains_new_store_and_closes_on_owner_thread(tmp_path, monkeypatch):
    path, appends, factory, _ = setup(tmp_path, monkeypatch)
    def fail(_): raise RuntimeError('bind failed')
    monkeypatch.setattr(module, 'UnixServer', fail)
    host = module.NativeGatewayHost(factory, registry=path, append_store=appends)
    with pytest.raises(DialogError, match='authority_host_failed'): host.start(seconds=2)
    assert host.finished.wait(2) and appends.exists()
    reopened = module.open_append_store(appends); reopened.close()


def test_lifespan_enters_original_then_host_and_closes_in_reverse(tmp_path, monkeypatch):
    events = []
    class Host:
        def __init__(self, factory): assert callable(factory)
        def start(self): events.append('host_start')
        def stop(self): events.append('host_stop')
    monkeypatch.setattr(module, 'NativeGatewayHost', Host)
    @asynccontextmanager
    async def original(app):
        events.append('original_start')
        try: yield {'kept': True}
        finally: events.append('original_stop')
    app, bridge = SimpleNamespace(router=SimpleNamespace(lifespan_context=original)), SimpleNamespace()
    module.install_lifespan(app, bridge, dialog_factory=lambda: None)
    with pytest.raises(DialogError, match='already_bound'):
        module.install_lifespan(app, bridge, dialog_factory=lambda: None)
    async def run():
        async with app.router.lifespan_context(app) as state:
            assert state == {'kept': True}
            events.append('application')
    asyncio.run(run())
    assert events == ['original_start','host_start','application','host_stop','original_stop']


def test_startup_failure_always_exits_original_lifespan(monkeypatch):
    events = []
    class Host:
        def __init__(self, factory): pass
        def start(self): raise DialogError('authority_git_binding_required')
        def stop(self): events.append('stop')
    monkeypatch.setattr(module, 'NativeGatewayHost', Host)
    @asynccontextmanager
    async def original(app):
        try: yield
        finally: events.append('original_closed')
    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=original))
    module.install_lifespan(app, SimpleNamespace(), dialog_factory=lambda: None)
    async def run():
        async with app.router.lifespan_context(app): raise AssertionError('not ready')
    with pytest.raises(DialogError): asyncio.run(run())
    assert events == ['stop','original_closed']


def test_start_timeout_never_claims_ready_or_creates_store_later(tmp_path, monkeypatch):
    path, appends, _, _ = setup(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def delayed():
        entered.set(); release.wait(2)
        return SimpleNamespace(repository='S960b/agent-dialogs', load=lambda task: None)
    host = module.NativeGatewayHost(delayed, registry=path, append_store=appends)
    try:
        with pytest.raises(DialogError, match='authority_start_unconfirmed'): host.start(seconds=0.01)
        assert not host.ready and not appends.exists()
        if entered.is_set():
            with pytest.raises(DialogError, match='authority_shutdown_unconfirmed'): host.stop(seconds=0.001)
    finally:
        release.set()
        assert host.finished.wait(2)
        host.thread.join(2)
    assert not appends.exists() and not host.thread.is_alive()


def test_both_actor_appends_use_the_lifespan_hosts_same_store(tmp_path, monkeypatch):
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, TASK
    from test_unix_authority import Channel, append
    monkeypatch.setattr(module, 'TASK_ID', TASK)
    api = FakeApi()
    path = tmp_path/'r.sqlite'
    registry = LifecycleRegistry(path); registry.register(TASK, api.initial); registry.close()
    complete, receipts = threading.Event(), []
    class Server:
        def __init__(self, runtime): self.runtime = runtime
        def serve_once(self, timeout):
            if complete.is_set():
                module.threading.Event().wait(0.001); return
            host = self.runtime
            first = Channel({**append(api.initial), 'task_id': TASK}, pid=host.bridge_pid)
            host.handle('chatgpt', first)
            receipts.append(first.result())
            if receipts[0]['ok']:
                second = Channel({**append(receipts[0]['result']['commit'], 'two', 'report'), 'task_id': TASK}, pid=host.bridge_pid+1)
                host.handle('mt', second); receipts.append(second.result())
            complete.set()
        def close(self): pass
    monkeypatch.setattr(module, 'UnixServer', Server)
    appends = tmp_path/'a.sqlite'
    host = module.NativeGatewayHost(lambda: GitHubDialog(api), registry=path, append_store=appends, task_id=TASK)
    host.start(seconds=2)
    try: assert complete.wait(2)
    finally: host.stop(seconds=2)
    assert len(receipts) == 2 and all(r['ok'] for r in receipts)
    conn = sqlite3.connect(appends)
    try:
        rows = conn.execute('SELECT message_id,attempted,confirmed_commit FROM appends ORDER BY message_id').fetchall()
        assert rows == [('one',1,receipts[0]['result']['commit']),('two',1,receipts[1]['result']['commit'])]
    finally: conn.close()


def test_notifications_bind_owner_durably_on_gateway_thread_before_dispatch(tmp_path, monkeypatch):
    from git_dialog.git_events import owner_digest, EventJournal
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, TASK
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path/'r'); registry.register(TASK, api.initial); registry.close()
    entered, release = threading.Event(), threading.Event()
    class Server:
        def __init__(self, runtime): self.runtime = runtime
        def serve_once(self, timeout):
            entered.set(); release.wait(0.005)
        def close(self): pass
    monkeypatch.setattr(module, 'UnixServer', Server)
    host = module.NativeGatewayHost(lambda:GitHubDialog(api), registry=tmp_path/'r',
                                   append_store=tmp_path/'a', task_id=TASK)
    host.configure_notifications(SimpleNamespace(), path=tmp_path/'e', checkpoint_head=api.initial, checkpoint_seq=0)
    with pytest.raises(DialogError): host.request_notification_owner('owner')
    host.start(seconds=2)
    try:
        assert host.request_notification_owner('owner').result(2) is True
        assert host.notification_owner == owner_digest('owner')
        with pytest.raises(DialogError): host.request_notification_owner('foreign')
        with pytest.raises(DialogError): host.request_notification_owner(None)
        assert host.ready
    finally: release.set(); host.stop(seconds=2)
    journal = EventJournal(tmp_path/'e', TASK, api.initial, 0)
    try: assert journal.owner_hash == owner_digest('owner')
    finally: journal.close()


def test_notification_activation_checkpoint_mismatch_creates_no_journal(tmp_path, monkeypatch):
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, TASK
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path/'r'); registry.register(TASK, api.initial); registry.close()
    host = module.NativeGatewayHost(lambda:GitHubDialog(api), registry=tmp_path/'r',
                                   append_store=tmp_path/'a', task_id=TASK)
    host.configure_notifications(SimpleNamespace(), path=tmp_path/'e', checkpoint_head='a'*40, checkpoint_seq=0)
    with pytest.raises(DialogError): host.start(seconds=2)
    assert host.finished.wait(2) and not (tmp_path/'e').exists()


def test_notification_journal_cannot_alias_existing_stores(tmp_path):
    host = module.NativeGatewayHost(lambda:None, registry=tmp_path/'r', append_store=tmp_path/'a')
    with pytest.raises(DialogError): host.configure_notifications(SimpleNamespace(), path=tmp_path/'a')


def test_p31_multi_notifications_initialize_and_bind_same_oauth_owner(tmp_path, monkeypatch):
    from git_dialog.git_events import EventJournal, owner_digest
    from git_dialog.multi_git_events import MultiEventJournal
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, TASK

    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / 'r')
    registry.register(TASK, api.initial)
    registry.close()

    class Server:
        def __init__(self, runtime):
            self.runtime = runtime
        def serve_once(self, timeout):
            threading.Event().wait(0.001)
        def close(self):
            pass

    monkeypatch.setattr(module, 'UnixServer', Server)
    host = module.NativeGatewayHost(
        lambda: GitHubDialog(api), registry=tmp_path / 'r',
        append_store=tmp_path / 'a', task_id=TASK)
    host.configure_notifications(
        SimpleNamespace(), path=tmp_path / 'p26',
        checkpoint_head=api.initial, checkpoint_seq=0,
        multi_delivery=SimpleNamespace(), multi_path=tmp_path / 'p31')
    host.start(seconds=2)
    try:
        assert host.request_notification_owner('owner').result(2) is True
        assert host.notification_owner == owner_digest('owner')
        assert host.ready
        assert (tmp_path / 'p26').exists() and (tmp_path / 'p31').exists()
    finally:
        host.stop(seconds=2)

    first = EventJournal(tmp_path / 'p26', TASK, api.initial, 0)
    second = MultiEventJournal(tmp_path / 'p31', TASK, 999)
    try:
        assert first.owner_hash == second.owner_hash == owner_digest('owner')
        assert second.checkpoint_row == 0
    finally:
        first.close()
        second.close()


def test_p31_multi_journal_alias_is_denied(tmp_path):
    host = module.NativeGatewayHost(lambda: None,
        registry=tmp_path / 'r', append_store=tmp_path / 'a')
    with pytest.raises(DialogError, match='authority_store_alias_rejected'):
        host.configure_notifications(SimpleNamespace(), path=tmp_path / 'p26',
            multi_delivery=SimpleNamespace(), multi_path=tmp_path / 'a')


def test_p323_retired_legacy_pilot_restores_host_without_remote_ref(tmp_path, monkeypatch):
    """Existing journal identity/checkpoints survive deletion of the pilot branch."""
    from git_dialog.github import GitHubDialog
    from git_dialog.git_events import EventJournal
    from git_dialog.multi_git_events import MultiEventJournal
    from test_github import FakeApi, TASK, REF

    api = FakeApi()
    registry_path = tmp_path / 'registry'
    registry = LifecycleRegistry(registry_path)
    registry.register(TASK, api.initial)
    registry.cancel(TASK)
    registry.close()
    del api.refs[REF]  # the legacy remote dialogue no longer exists
    p26, p31 = tmp_path / 'p26', tmp_path / 'p31'
    old = EventJournal(p26, TASK, api.initial, 0)
    old.close()
    multi = MultiEventJournal(p31, TASK, 0)
    multi.close()

    class Server:
        def __init__(self, runtime):
            assert runtime.common.task_id == TASK
            assert runtime.common.registry.get(TASK)['state'] == 'cancelled'
        def serve_once(self, timeout):
            threading.Event().wait(0.001)
        def close(self):
            pass

    monkeypatch.setattr(module, 'UnixServer', Server)
    host = module.NativeGatewayHost(
        lambda: GitHubDialog(api), registry=registry_path,
        append_store=tmp_path / 'appends', task_id=TASK)
    host.configure_notifications(
        SimpleNamespace(), path=p26, checkpoint_head=api.initial,
        checkpoint_seq=0, multi_delivery=SimpleNamespace(), multi_path=p31)
    host.start(seconds=2)
    try:
        assert host.ready
    finally:
        host.stop(seconds=2)
    # No new reference, no implicit re-registration, no changed route.
    assert REF not in api.refs
    assert all(m == 'GET' for m, _, _ in api.calls)
    with sqlite3.connect(p26) as conn:
        assert conn.execute('SELECT task,head,seq FROM route').fetchone() == (TASK, api.initial, 0)
    with sqlite3.connect(p31) as conn:
        assert conn.execute('SELECT pilot,checkpoint FROM route').fetchone() == (TASK, 0)


def test_p323_retired_pilot_cannot_initialize_fresh_event_checkpoint(tmp_path, monkeypatch):
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, TASK, REF

    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / 'registry')
    registry.register(TASK, api.initial)
    registry.cancel(TASK)
    registry.close()
    del api.refs[REF]
    host = module.NativeGatewayHost(lambda: GitHubDialog(api),
        registry=tmp_path / 'registry', append_store=tmp_path / 'appends', task_id=TASK)
    host.configure_notifications(SimpleNamespace(), path=tmp_path / 'new-p26',
        checkpoint_head=api.initial, checkpoint_seq=0)
    with pytest.raises(DialogError, match='authority_host_failed'):
        host.start(seconds=2)
    assert not (tmp_path / 'new-p26').exists()


def test_p323_retired_pilot_rejects_non_private_repository(tmp_path, monkeypatch):
    from git_dialog.github import GitHubDialog
    from test_github import FakeApi, TASK, REF

    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / 'registry')
    registry.register(TASK, api.initial)
    registry.cancel(TASK)
    registry.close()
    del api.refs[REF]
    api.private = False
    host = module.NativeGatewayHost(lambda: GitHubDialog(api),
        registry=tmp_path / 'registry', append_store=tmp_path / 'appends', task_id=TASK)
    with pytest.raises(DialogError, match='authority_host_failed'):
        host.start(seconds=2)
    assert not (tmp_path / 'appends').exists()

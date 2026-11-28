import json
import sqlite3

import pytest

from git_dialog import DialogError
from git_dialog.append_store import AppendStore
from git_dialog.authority import CommonAuthority, WRITERS
from git_dialog.gateway import WriteGateway
from git_dialog.github import ApiError, GitHubDialog
from git_dialog.ingress import AccessAuthority
from git_dialog.journal import ExecutionJournal
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.worker import WorkerStep
from test_github import FakeApi, TASK, STAMP, writes
from test_ingress import request
from test_confirmation import setup as confirmation_setup, cleanup


@pytest.fixture
def shared(tmp_path):
    directory = tmp_path / 'shared'
    directory.mkdir(mode=0o700)
    api = FakeApi()
    registry = LifecycleRegistry(directory / 'lifecycle.sqlite')
    registry.register(TASK, api.initial)
    store = AppendStore(directory / 'append.sqlite')
    common = CommonAuthority(GitHubDialog(api), registry, store, TASK)
    yield api, registry, store, common, directory
    store.close()
    registry.close()


def send(common, head, *, writer='chatgpt', mid='m1', body='data', stamp=STAMP):
    actor = WRITERS[writer]
    return common.port(writer).append(TASK, head, message_id=mid, actor=actor,
        kind='task' if actor == 'chatgpt' else 'report', body=body, created_at_utc=stamp)


def test_all_configured_writers_use_same_registry_and_store(shared):
    api, registry, store, common, _ = shared
    head = api.initial
    for i, writer in enumerate(('chatgpt', 'mt', 'canary_chatgpt', 'canary_mt', 'chatgpt', 'worker')):
        port = common.port(writer)
        assert port.registry is registry and port.durable_publication
        receipt = send(common, head, writer=writer, mid='m' + str(i))
        assert receipt.created
        assert store.status(TASK, 'm' + str(i)) == {
            'task_id': TASK, 'message_id': 'm' + str(i), 'actor': port.actor,
            'attempted': True, 'confirmed_commit': receipt.commit_sha}
        head = receipt.commit_sha
    assert len([c for c in api.calls if c[0] == 'PATCH']) == 6
    assert common.capabilities()['external_writers_fenced'] is False
    assert common.capabilities()['remote_adapter_integrated'] is False
    assert common.capabilities()['delete_enabled'] is False


def test_grant_visible_on_separate_connection_before_first_remote_write(shared):
    api, _, _, common, directory = shared
    seen = []
    def hook(method, path, payload):
        if method != 'GET':
            with sqlite3.connect(directory / 'append.sqlite') as observer:
                row = observer.execute('SELECT attempted,confirmed_commit FROM appends').fetchone()
            assert row == (1, None)
            seen.append(method)
    api.hook = hook
    send(common, api.initial)
    assert seen == ['POST', 'POST', 'PATCH']


def test_delayed_same_id_is_read_only_and_other_body_conflicts(shared):
    api, _, store, common, _ = shared
    first = send(common, api.initial)
    send(common, first.commit_sha, writer='mt', mid='reply')
    api.calls.clear()
    assert send(common, api.initial).created is False  # Saved intent, not a new send on stale head.
    assert not writes(api)
    with pytest.raises(DialogError, match='message_id_conflict'):
        send(common, api.initial, body='changed')
    assert not writes(api)
    assert store.status(TASK, 'm1')['confirmed_commit'] == first.commit_sha


@pytest.mark.parametrize('actor,kind', [('mt', 'report'), ('chatgpt', 'report')])
def test_changed_actor_or_kind_cannot_reuse_reserved_id(shared, actor, kind):
    api, _, _, common, _ = shared
    send(common, api.initial)
    api.calls.clear()
    with pytest.raises(DialogError, match='message_id_conflict'):
        common.gateway.port(actor).append(TASK, api.initial, message_id='m1', actor=actor,
                                          kind=kind, body='data', created_at_utc=STAMP)
    assert not writes(api)


@pytest.mark.parametrize('effect', [False, True])
def test_restart_after_lost_response_never_sends_again(shared, effect):
    api, registry, store, common, directory = shared
    original = api.request
    def lost(method, path, payload=None):
        if method == 'PATCH':
            if effect: original(method, path, payload)
            raise ApiError()
        return original(method, path, payload)
    api.request = lost
    with pytest.raises(DialogError, match='publication_unknown'):
        send(common, api.initial)
    assert store.status(TASK, 'm1')['attempted']
    # A distinct owner connection models reopening, with the same durable files.
    reopened = AppendStore(directory / 'append.sqlite')
    try:
        again = CommonAuthority(GitHubDialog(api), registry, reopened, TASK)
        api.calls.clear()
        if effect:
            assert send(again, api.initial).created is False
            assert reopened.status(TASK, 'm1')['confirmed_commit']
        else:
            with pytest.raises(DialogError, match='gateway_publication_unresolved'):
                send(again, api.initial)
        assert not writes(api)
    finally:
        reopened.close()


def test_failure_before_git_object_creation_also_consumes_grant(shared):
    api, _, store, common, _ = shared
    original = api.request
    def failed(method, path, payload=None):
        if method == 'POST': raise ApiError(503)
        return original(method, path, payload)
    api.request = failed
    with pytest.raises(ApiError): send(common, api.initial)
    assert store.status(TASK, 'm1')['attempted']
    api.calls.clear()
    with pytest.raises(DialogError, match='gateway_publication_unresolved'):
        send(common, api.initial)
    assert not writes(api)


@pytest.mark.parametrize('state', ['cancelled', 'closing', 'tombstoned'])
def test_one_registry_blocks_every_configured_writer_before_network(shared, state):
    api, registry, store, common, _ = shared
    if state == 'cancelled': registry.cancel(TASK)
    else:
        intent = registry.request_delete(TASK, api.initial)
        if state == 'tombstoned': registry.confirm_delete(TASK, intent['confirmation'], api.initial)
    api.calls.clear()
    for writer in WRITERS:
        with pytest.raises(DialogError, match='dialog_not_writable'):
            send(common, api.initial, writer=writer, mid=writer)
    assert not api.calls and store.conn.execute('SELECT count(*) FROM appends').fetchone()[0] == 0


@pytest.mark.parametrize('writer', ['owner', 'admin', 'foreign', None, {'actor': 'chatgpt'}])
def test_unknown_binding_is_not_an_identity(shared, writer):
    api, _, _, common, _ = shared
    with pytest.raises(DialogError, match='writer_binding_denied'): common.port(writer)
    assert not api.calls


def test_worker_requires_shared_port_and_publishes_through_it(shared, tmp_path):
    api, registry, store, common, _ = shared
    body = '{"protocol":"git-dialog-action","version":1,"op":"ping"}'
    send(common, api.initial, body=body)
    journal = ExecutionJournal(tmp_path / 'execution.sqlite')
    try:
        raw = GitHubDialog(api)
        with pytest.raises(DialogError, match='worker_shared_authority_required'):
            WorkerStep(journal, raw, registry, TASK, require_shared=True)
        weak = WriteGateway(raw, registry, TASK, actors=('mt',))
        with pytest.raises(DialogError, match='worker_shared_authority_required'):
            WorkerStep(journal, weak.port('mt'), registry, TASK, require_shared=True)
        result = common.worker(journal, allowed_ops=('ping',)).step('m1', 'result', 'run')
        assert result['status'] == 'published'
        assert json.loads(result['result'])['value'] == 'pong'
        assert store.status(TASK, 'result')['attempted']
        api.calls.clear()
        common.worker(journal, allowed_ops=('ping',)).step('m1', 'result', 'run')
        assert not writes(api)
    finally:
        journal.close()


def test_existing_ingress_routes_both_agents_via_common_store(tmp_path):
    api, registry, box, confirmations, controller = confirmation_setup(tmp_path)
    store = AppendStore(tmp_path / 'append.sqlite')
    try:
        common = CommonAuthority(controller.coordinator.dialog, registry, store, TASK)
        access = AccessAuthority({'cg': {'role': 'agent', 'actor': 'chatgpt'},
                                  'mt': {'role': 'agent', 'actor': 'mt'}})
        dispatcher = common.dispatcher(controller, access, origin='http://127.0.0.1:18878', mutations=True)
        first = request(dispatcher, access.issue('cg', now=100),
            {'op': 'append', 'head': api.initial, 'message_id': 'rpc1', 'kind': 'task', 'body': 'data'})
        second = request(dispatcher, access.issue('mt', now=100),
            {'op': 'append', 'head': first['commit'], 'message_id': 'rpc2', 'kind': 'report', 'body': 'result'})
        assert store.status(TASK, 'rpc2')['confirmed_commit'] == second['commit']
        assert first['created'] and second['created']
        assert not any(c[0] == 'DELETE' for c in api.calls)
    finally:
        store.close(); cleanup(registry, box, confirmations)


def test_missing_registration_not_created_by_common_authority(tmp_path):
    api = FakeApi()
    registry = LifecycleRegistry(tmp_path / 'registry.sqlite')
    store = AppendStore(tmp_path / 'append.sqlite')
    try:
        with pytest.raises(DialogError, match='unregistered_dialog'):
            CommonAuthority(GitHubDialog(api), registry, store, TASK)
        assert not api.calls
    finally:
        registry.close(); store.close()


@pytest.mark.parametrize('case', ['wrong_db', 'mode', 'hardlink', 'symlink'])
def test_append_store_rejects_unrelated_or_unsafe_database(tmp_path, case):
    path = tmp_path / 'append.sqlite'
    if case == 'wrong_db':
        with sqlite3.connect(path) as connection: connection.execute('CREATE TABLE unrelated(x)')
        path.chmod(0o600)
    else:
        store = AppendStore(path); store.close()
        if case == 'mode': path.chmod(0o644)
        if case == 'hardlink': (tmp_path / 'alias').hardlink_to(path)
        if case == 'symlink':
            path.rename(tmp_path / 'real.sqlite')
            path.symlink_to(tmp_path / 'real.sqlite')
    with pytest.raises(DialogError): AppendStore(path)


def test_append_store_no_body_or_private_paths_in_status(shared):
    api, _, store, common, _ = shared
    send(common, api.initial, body='untrusted text remains data')
    assert set(store.status(TASK, 'm1')) == {'task_id', 'message_id', 'actor', 'attempted', 'confirmed_commit'}

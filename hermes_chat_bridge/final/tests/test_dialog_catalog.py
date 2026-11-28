"""Offline multi-dialogue contract; no credentials, network or live branches."""
import asyncio
import copy
import json
import os
import stat

import pytest

from git_dialog import DialogError, TaskState, branch_ref, parse_snapshot
from git_dialog.append_store import AppendStore
from git_dialog.authority import CommonAuthority
from git_dialog.dialog_catalog import CreationStore, DialogCatalog, creation_identity, PAGE_BYTES
from git_dialog.github import ApiError, GitHubDialog, canonical
from git_dialog.lifecycle import LifecycleRegistry
from git_dialog.native_gateway import register_mcp
from git_dialog.unix_authority import Runtime
from test_github import BASE, REF, FakeApi, TASK, STAMP, writes
from test_unix_authority import Channel


class CreationApi(FakeApi):
    def request(self, method, path, payload=None):
        if method == 'POST' and path == BASE + '/git/trees' and 'base_tree' not in payload:
            self.calls.append((method, path, copy.deepcopy(payload)))
            if self.hook: self.hook(method, '/git/trees', payload)
            assert set(payload) == {'tree'}
            entries = {}
            for entry in payload['tree']:
                assert entry['mode'] == '100644' and entry['type'] == 'blob'
                entries[entry['path']] = self.entry(entry['content'])
            return {'sha': self.tree(entries)}
        if method == 'POST' and path == BASE + '/git/refs':
            self.calls.append((method, path, copy.deepcopy(payload)))
            if self.hook: self.hook(method, '/git/refs', payload)
            assert set(payload) == {'ref', 'sha'}
            if payload['ref'] in self.refs: raise ApiError(422)
            self.refs[payload['ref']] = payload['sha']
            return {'ref': payload['ref'], 'object': {'type': 'commit', 'sha': payload['sha']}}
        return super().request(method, path, payload)


@pytest.fixture
def catalog(tmp_path):
    api = CreationApi()
    registry = LifecycleRegistry(tmp_path/'r.sqlite'); registry.register(TASK, api.initial)
    appends = AppendStore(tmp_path/'a.sqlite')
    creations = CreationStore(tmp_path/'c.sqlite')
    service = DialogCatalog(GitHubDialog(api), registry, appends, creations)
    yield api, service
    creations.close(); appends.close(); registry.close()


@pytest.mark.parametrize('actor', ['chatgpt', 'mt'])
def test_either_peer_opens_root_with_own_first_turn_and_no_inherited_history(catalog, actor):
    api, service = catalog
    receipt = service.create(actor, 'new_chat_1', 'Separate task')
    task = receipt['task_id']
    assert receipt == {'request_id': 'new_chat_1', 'task_id': task, 'branch': branch_ref(task),
        'initiator': actor, 'status': 'confirmed', 'head': receipt['head'],
        'waiting_for': actor, 'created': True, 'retry_authorized': False}
    assert task != TASK and api.refs[REF] == api.initial
    assert api.commits[receipt['head']]['parents'] == []
    tree = api.trees[api.commits[receipt['head']]['tree']['sha']]
    assert set(tree) == {'task.json', 'state.json'}  # no pilot, README, code or messages
    snap = service.dialog.load(task)
    assert snap.messages == [] and snap.state.next_actor == actor
    first = service.append(actor, task, head=receipt['head'], message_id='first', kind='task', body='inert')
    assert service.dialog.load(task).last.sender == actor
    assert service.appends.status(task, 'first')['confirmed_commit'] == first['commit']
    assert len([c for c in api.calls if c[0] == 'POST' and c[1].endswith('/git/refs')]) == 1


def test_two_chats_and_both_actors_never_share_branch_turn_or_append_grant(catalog):
    api, service = catalog
    a = service.create('chatgpt', 'chat_a', 'A')
    b = service.create('mt', 'chat_b', 'B')
    original = dict(api.refs)
    service.append('chatgpt', a['task_id'], head=a['head'], message_id='same_id', kind='task', body='A only')
    assert api.refs['refs/heads/' + b['branch']] == original['refs/heads/' + b['branch']]
    service.append('mt', b['task_id'], head=b['head'], message_id='same_id', kind='task', body='B only')
    assert service.view(a['task_id'])['history'][0]['body'] == 'A only'
    assert service.view(b['task_id'])['history'][0]['body'] == 'B only'
    assert service.appends.status(a['task_id'], 'same_id')['actor'] == 'chatgpt'
    assert service.appends.status(b['task_id'], 'same_id')['actor'] == 'mt'
    assert api.refs[REF] == api.initial


def test_repeat_creation_after_response_or_restart_is_get_only(catalog, tmp_path):
    api, service = catalog
    created = service.create('chatgpt', 'stable_chat_id', 'One chat')
    service.creations.close()
    service.creations = CreationStore(tmp_path/'c.sqlite')
    api.calls.clear()
    again = service.create('chatgpt', 'stable_chat_id', 'One chat')
    assert again['task_id'] == created['task_id'] and not again['created']
    assert not writes(api)
    assert service.creation_status('chatgpt', 'stable_chat_id')['status'] == 'confirmed'


def test_same_request_different_title_is_conflict_not_second_branch(catalog):
    api, service = catalog
    service.create('mt', 'one', 'First')
    api.calls.clear()
    with pytest.raises(DialogError, match='creation_request_conflict'):
        service.create('mt', 'one', 'Different')
    assert not api.calls


def test_same_request_name_from_two_peers_is_not_impersonation(catalog):
    _, service = catalog
    cg = service.create('chatgpt', 'my_chat', 'A')
    mt = service.create('mt', 'my_chat', 'B')
    assert cg['task_id'] != mt['task_id']
    assert cg['waiting_for'] == 'chatgpt' and mt['waiting_for'] == 'mt'


def test_lost_new_ref_ack_reconciles_without_second_post(catalog):
    api, service = catalog
    real = api.request
    def lost(method, path, payload=None):
        result = real(method, path, payload)
        if method == 'POST' and path.endswith('/git/refs'): raise ApiError()
        return result
    api.request = lost
    with pytest.raises(DialogError, match='creation_unconfirmed'):
        service.create('chatgpt', 'lost_ack', 'Keep identity')
    row = service.creations.get('chatgpt', 'lost_ack')
    assert row['attempted'] and row['candidate'] and not row['confirmed']
    with pytest.raises(DialogError, match='unregistered_dialog'): service.registry.get(row['task_id'])
    api.calls.clear()
    result = service.creation_status('chatgpt', 'lost_ack')
    assert result['status'] == 'confirmed' and result['task_id'] == row['task_id']
    assert not writes(api)


@pytest.mark.parametrize('damage', ['missing', 'wrong_ref', 'wrong_sha', 'wrong_type'])
def test_malformed_create_ack_does_not_grant_a_second_publication(catalog, damage):
    api, service = catalog
    real = api.request
    def broken(method, path, payload=None):
        result = real(method, path, payload)
        if method == 'POST' and path.endswith('/git/refs'):
            if damage == 'missing': return {}
            if damage == 'wrong_ref': result['ref'] = REF
            if damage == 'wrong_sha': result['object']['sha'] = 'a'*40
            if damage == 'wrong_type': result['object']['type'] = 'tree'
        return result
    api.request = broken
    with pytest.raises(DialogError, match='creation_unconfirmed'):
        service.create('mt', 'bad_ack', 'Task')
    api.calls.clear()
    assert service.creation_status('mt', 'bad_ack')['status'] == 'confirmed'
    assert not writes(api)


def test_selected_writer_status_is_scoped_and_does_not_fall_back_to_pilot(catalog):
    api, service = catalog
    r = service.create('mt', 'status', 'Task')
    service.append('mt', r['task_id'], head=r['head'], message_id='report', kind='report', body='inert')
    api.calls.clear()
    obj = {'op': 'writer_status', 'task_id': r['task_id'], 'message_id': 'report'}
    result = service.call('mt', obj)
    assert result['task_id'] == r['task_id'] and result['attempted'] and result['actor'] == 'mt'
    with pytest.raises(DialogError): service.call('chatgpt', obj)
    with pytest.raises(DialogError): service.call('mt', {**obj, 'task_id': TASK})
    assert not api.calls


@pytest.mark.parametrize('at', ['tree', 'commit', 'ref_before_effect'])
def test_crash_during_creation_never_reissues_objects_or_branch(catalog, at):
    api, service = catalog
    def fail(method, suffix, payload):
        targets = {'tree': '/git/trees', 'commit': '/git/commits', 'ref_before_effect': '/git/refs'}
        if method == 'POST' and suffix == targets[at]: raise ApiError()
    api.hook = fail
    with pytest.raises((ApiError, DialogError)): service.create('mt', 'crash', 'Keep stopped')
    assert service.creations.get('mt', 'crash')['attempted'] == 1
    api.calls.clear()
    try: service.create('mt', 'crash', 'Keep stopped')
    except DialogError: pass
    assert not writes(api)
    assert set(api.refs) == {REF}


@pytest.mark.parametrize('state', ['cancelled', 'closing', 'tombstoned'])
def test_retired_task_never_reactivated_by_repeat_create_or_status(catalog, state):
    api, service = catalog
    r = service.create('mt', 'retire', 'Task')
    if state == 'cancelled': service.registry.cancel(r['task_id'])
    else:
        intent = service.registry.request_delete(r['task_id'], r['head'])
        if state == 'tombstoned':
            service.registry.confirm_delete(r['task_id'], intent['confirmation'], r['head'])
    api.calls.clear()
    with pytest.raises(DialogError, match='task_id_retired'): service.create('mt', 'retire', 'Task')
    assert service.registry.get(r['task_id'])['state'] == state and not writes(api)


def test_deleted_branch_is_not_recreated_by_any_repeat_or_append(catalog):
    api, service = catalog
    r = service.create('chatgpt', 'delete', 'Task')
    del api.refs['refs/heads/' + r['branch']]
    api.calls.clear()
    for action in (lambda: service.create('chatgpt', 'delete', 'Task'),
                   lambda: service.creation_status('chatgpt', 'delete'),
                   lambda: service.append('chatgpt', r['task_id'], head=r['head'], message_id='x', kind='task', body='data')):
        with pytest.raises(DialogError): action()
    assert not writes(api)


def test_cross_branch_head_wrong_turn_and_unregistered_ids_never_write(catalog):
    api, service = catalog
    a = service.create('chatgpt', 'a', 'A')
    b = service.create('mt', 'b', 'B')
    api.calls.clear()
    for actor, task, head in [('chatgpt', a['task_id'], b['head']),
                              ('mt', a['task_id'], a['head']),
                              ('chatgpt', 'task_not_registered', a['head'])]:
        with pytest.raises(DialogError):
            service.append(actor, task, head=head, message_id='bad', kind='task', body='inert')
    assert not writes(api)


def test_cancelled_dialogue_keeps_readable_history_but_no_new_publication(catalog):
    api, service = catalog
    r = service.create('chatgpt', 'archive', 'History')
    service.registry.cancel(r['task_id'])
    api.calls.clear()
    assert service.view(r['task_id'])['task_id'] == r['task_id']
    with pytest.raises(DialogError):
        service.append('chatgpt', r['task_id'], head=r['head'], message_id='bad', kind='task', body='inert')
    assert not writes(api)


def test_response_size_is_bounded_without_truncating_or_repeating_messages(catalog, monkeypatch):
    from git_dialog import Message
    _, service = catalog
    r = service.create('chatgpt', 'large', 'Large')
    messages = []
    for seq in range(1, 21):
        actor = 'chatgpt' if seq % 2 else 'mt'
        messages.append(Message(r['task_id'], f'm{seq}', seq, actor,
            'mt' if actor == 'chatgpt' else 'chatgpt', f'm{seq-1}' if seq > 1 else None,
            'report', STAMP, 'Я'*30000))
    task = service.dialog.load(r['task_id']).task
    snapshot = parse_snapshot(task, TaskState(r['task_id'], 20, 20, 'm20', 'chatgpt', 'working').to_dict(),
                              [m.to_dict() for m in messages], 'a'*40)
    monkeypatch.setattr(service.dialog, 'load', lambda task_id: snapshot)
    first = service.view(r['task_id'], limit=100)
    assert len(json.dumps(first).encode()) < 512*1024
    assert 0 < len(first['history']) < 20
    assert first['history'][0]['body'] == 'Я'*30000
    second = service.view(r['task_id'], offset=first['next_offset'], limit=100)
    assert second['history'][0]['seq'] == 20 - len(first['history'])


def test_ref_collision_never_adopts_or_overwrites_foreign_history(catalog):
    api, service = catalog
    task = creation_identity('chatgpt', 'collide', 'Title')
    api.refs['refs/heads/' + branch_ref(task)] = api.initial
    api.calls.clear()
    with pytest.raises(DialogError, match='dialog_already_exists'):
        service.create('chatgpt', 'collide', 'Title')
    assert not writes(api) and api.refs['refs/heads/' + branch_ref(task)] == api.initial


def test_lists_metadata_without_loading_any_history_newest_first(catalog):
    api, service = catalog
    a = service.create('chatgpt', 'a', 'A')
    b = service.create('mt', 'b', 'B')
    api.calls.clear()
    page = service.list(limit=1)
    assert [r['task_id'] for r in page['dialogs']] == [b['task_id']]
    assert page['next_offset'] == 1 and page['dialogs'][0]['title'] == 'B'
    assert service.list(offset=1, limit=1)['dialogs'][0]['task_id'] == a['task_id']
    assert not api.calls and 'history' not in repr(page)


def test_history_latest_first_pagination_and_explicit_old_sequence_mode(catalog):
    _, service = catalog
    r = service.create('mt', 'chat', 'Thread')
    task, head = r['task_id'], r['head']
    for seq, actor in enumerate(('mt', 'chatgpt', 'mt', 'chatgpt', 'mt'), 1):
        head = service.append(actor, task, head=head, message_id=f'm{seq}', kind='report', body=str(seq))['commit']
    assert [m['seq'] for m in service.view(task, limit=2)['history']] == [5, 4]
    assert [m['seq'] for m in service.view(task, offset=2, limit=2)['history']] == [3, 2]
    exact = service.view(task, offset=3, limit=1, order='oldest')
    assert exact['history'][0]['seq'] == 4 and exact['next_offset'] == 4


@pytest.mark.parametrize('obj', [
    {'op': 'view', 'offset': 0, 'limit': 20},
    {'op': 'view', 'task_id': 'task_missing', 'offset': 0, 'limit': 20, 'order': 'newest'},
    {'op': 'create_dialog', 'request_id': 'x', 'title': 'T', 'actor': 'mt'},
    {'op': 'create_dialog', 'request_id': 'x', 'title': 'T', 'repository': 'foreign/repo'},
    {'op': 'create_dialog', 'request_id': 'x', 'title': 'T', 'task_id': TASK},
    {'op': 'confirm_close'}, {'op': 'create_dialog', 'request_id': '../bad', 'title': 'T'},
])
def test_missing_selection_and_capability_injection_denied_before_network(catalog, obj):
    api, service = catalog
    with pytest.raises(DialogError): service.call('chatgpt', obj)
    assert not api.calls


@pytest.mark.parametrize('title', ['', ' ', 'x'*257, 'line\nbreak', '\ud800'])
def test_invalid_title_denied_before_intent_or_network(catalog, title):
    api, service = catalog
    with pytest.raises(DialogError): service.create('mt', 'id', title)
    assert not api.calls and service.creations.conn.execute('SELECT COUNT(*) FROM creations').fetchone()[0] == 0


def test_two_peer_channels_multi_runtime_auth_before_creation_and_explicit_task(catalog):
    api, service = catalog
    common = CommonAuthority(service.dialog, service.registry, service.appends, TASK)
    runtime = Runtime(common, bridge_pid=101, catalog=service)
    bad = Channel({'op': 'create_dialog', 'request_id': 'bad', 'title': 'No'}, pid=102)
    runtime.handle('chatgpt', bad)
    assert bad.result()['ok'] is False and not api.calls
    results = []
    for actor, pid in (('chatgpt', 101), ('mt', 102)):
        conn = Channel({'op': 'create_dialog', 'request_id': actor+'_chat', 'title': actor}, pid=pid)
        runtime.handle(actor, conn)
        assert conn.result()['ok'] is True
        r = conn.result()['result']; results.append(r)
        msg = Channel({'op': 'append', 'task_id': r['task_id'], 'head': r['head'],
            'message_id': 'one', 'kind': 'task', 'body': actor}, pid=pid)
        runtime.handle(actor, msg)
        assert msg.result()['ok'] is True
    assert results[0]['task_id'] != results[1]['task_id'] and api.refs[REF] == api.initial
    api.calls.clear()
    missing = Channel({'op': 'view', 'offset': 0, 'limit': 1})
    runtime.handle('chatgpt', missing)
    assert missing.result()['ok'] is False and not api.calls


def test_mcp_exposes_per_chat_creation_and_requires_task_on_read_and_write(monkeypatch):
    handlers, calls = {}, []
    class MCP:
        def tool(self, **kw):
            def save(fn): handlers[fn.__name__] = fn; return fn
            return save
    class Bridge:
        allowed = False
        def require_owner(self):
            calls.append('auth')
            if not self.allowed: raise PermissionError()
            return 'owner'
    class Client:
        def __init__(self, path): pass
        def call(self, obj): calls.append(obj); return {'result': 'data'}
    monkeypatch.setattr('git_dialog.native_gateway.UnixClient', Client)
    bridge = Bridge(); register_mcp(MCP(), bridge)
    assert set(handlers) == {'dialog_create', 'dialog_creation_status', 'dialog_list', 'dialog_index', 'dialog_health', 'dialog_notice'}
    with pytest.raises(PermissionError): asyncio.run(handlers['dialog_create']('req', 'Title'))
    assert calls == ['auth']
    bridge.allowed = True
    asyncio.run(handlers['dialog_create']('req', 'Title'))
    asyncio.run(handlers['dialog_index']('task_one'))
    asyncio.run(handlers['dialog_health']('task_one'))
    asyncio.run(handlers['dialog_notice']('task_one', 'a'*40, 'm', 1))
    assert calls[2] == {'op': 'create_dialog', 'request_id': 'req', 'title': 'Title'}
    assert calls[4] == {'op': 'index', 'task_id': 'task_one', 'offset': 0, 'limit': 20, 'order': 'newest'}
    assert calls[6] == {'op': 'health', 'task_id': 'task_one'}
    assert calls[8] == {'op': 'notice', 'task_id': 'task_one', 'head': 'a'*40,
                        'message_id': 'm', 'seq': 1}
    assert 'body' not in repr(calls[8])
    with pytest.raises(TypeError): handlers['dialog_index']()


@pytest.mark.parametrize('kind', ['empty', 'foreign', 'symlink', 'hardlink', 'mode'])
def test_creation_store_rejects_unsafe_existing_files(tmp_path, kind):
    path = tmp_path/'c.sqlite'
    if kind == 'symlink':
        target = tmp_path/'target'; target.touch(mode=0o600); path.symlink_to(target)
    elif kind == 'hardlink':
        target = tmp_path/'target'; target.touch(mode=0o600); os.link(target, path)
    else:
        path.touch(mode=0o600)
        if kind == 'mode': path.chmod(0o644)
        if kind == 'foreign':
            import sqlite3
            conn = sqlite3.connect(path); conn.execute('CREATE TABLE unrelated(x)'); conn.close()
    before = path.read_bytes()
    with pytest.raises(DialogError): CreationStore(path)
    assert path.read_bytes() == before


def test_new_store_is_private_and_registry_schema_unmodified(catalog, tmp_path):
    _, service = catalog
    assert stat.S_IMODE((tmp_path/'c.sqlite').stat().st_mode) == 0o600
    tables = {r[0] for r in service.registry.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {'dialogs'}

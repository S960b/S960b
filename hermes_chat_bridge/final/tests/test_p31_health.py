"""P31 per-dialog health: read-only MCP/Unix state and safe error codes."""
import json

import pytest

from git_dialog import DialogError
from git_dialog.authority import CommonAuthority
from git_dialog.dialog_catalog import DialogCatalog
from git_dialog.unix_authority import Runtime
from test_dialog_catalog import catalog
from test_github import TASK, writes
from test_unix_authority import Channel


def test_health_only_returns_bounded_state_no_body_and_no_writes(catalog):
    api, service = catalog
    created = service.create('chatgpt', 'health_test', 'Health test')
    task = created['task_id']
    api.calls.clear()
    health = service.health(task)
    assert health == {
        'task_id': task, 'head': created['head'],
        'status': 'waiting', 'waiting_for': 'chatgpt',
        'stalled': False, 'seconds_since_message': None,
        'last_message_at': None, 'last_error_code': None,
    }
    assert 'history' not in health and 'body' not in json.dumps(health)
    assert not writes(api)
    with pytest.raises(DialogError, match='catalog_request_rejected'):
        service.health(task, last_error_code='PASSWORD_IN_MEMORY')


def test_unix_health_is_task_scoped_and_tracks_safe_in_process_code(catalog):
    api, service = catalog
    created = service.create('chatgpt', 'health_error', 'Health')
    task = created['task_id']
    runtime = Runtime(CommonAuthority(service.dialog, service.registry,
                                     service.appends, TASK),
                      bridge_pid=101, catalog=service)
    api.calls.clear()
    bad = Channel({'op': 'append', 'task_id': task, 'head': created['head'],
                   'message_id': 'not_our_turn', 'kind': 'report',
                   'body': 'MUST_NOT_APPEAR_IN_HEALTH'}, pid=102)
    runtime.handle('mt', bad)
    assert bad.result() == {'ok': False, 'result': {
        'error': 'authority_result_unconfirmed', 'retry_authorized': False,
        'phase': 'dispatch', 'code': 'wrong_turn',
    }}
    assert runtime._last_errors[task] == 'wrong_turn'
    health_request = {'op': 'health', 'task_id': task}
    channel = Channel(health_request, pid=101)
    runtime.handle('chatgpt', channel)
    assert channel.result()['ok'] is True
    health = channel.result()['result']
    assert health['task_id'] == task
    assert health['head'] == created['head']
    assert health['waiting_for'] == 'chatgpt'
    assert health['last_error_code'] == 'wrong_turn'
    assert health['last_message_at'] is None
    assert 'MUST_NOT_APPEAR' not in json.dumps(health)
    assert not writes(api)
    # No fallback to the old pilot or any other dialogue.
    missing = Channel({'op': 'health'}, pid=101)
    runtime.handle('chatgpt', missing)
    assert missing.result()['ok'] is False
    injected = Channel({'op': 'health', 'task_id': task, 'actor': 'mt'}, pid=101)
    runtime.handle('chatgpt', injected)
    assert injected.result()['ok'] is False
    unauthenticated = Channel(health_request, pid=102)
    runtime.handle('chatgpt', unauthenticated)
    assert unauthenticated.result()['ok'] is False
    assert not writes(api)


def test_health_last_error_is_process_local_and_bounded(catalog):
    api, service = catalog
    runtime = Runtime(CommonAuthority(service.dialog, service.registry,
                                     service.appends, TASK),
                      bridge_pid=101, catalog=service)
    assert runtime._last_errors == {}
    for i in range(150):
        task = 'task_' + f'{i:032x}'
        frame = Channel({'op': 'view', 'task_id': task,
                         'offset': 0, 'limit': 1, 'order': 'newest'}, pid=101)
        runtime.handle('chatgpt', frame)
        assert frame.result()['ok'] is False
    assert len(runtime._last_errors) == 128
    assert list(runtime._last_errors.values()) == ['not_found'] * 128
    fresh = Runtime(CommonAuthority(service.dialog, service.registry,
                                   service.appends, TASK),
                    bridge_pid=101, catalog=service)
    assert not fresh._last_errors

import io
import json
import sqlite3
import threading

import pytest

from git_dialog import DialogError
from git_dialog import authority_writers as cli
from git_dialog.gateway_lifespan import open_worker_journal
from git_dialog.github import ApiError
from git_dialog.unix_authority import Runtime
from test_github import TASK, writes
from test_shared_authority import shared, send
from test_unix_authority import Channel


ACTION = '{"protocol":"git-dialog-action","version":1,"op":"sha256","text":"P23 shared authority"}'


def setup(shared):
    api, registry, store, common, directory = shared
    calls = []
    def factory():
        calls.append(threading.get_ident())
        return open_worker_journal(directory / 'worker.sqlite')
    runtime = Runtime(common, bridge_pid=101, journal_factory=factory)
    return api, registry, store, common, directory, calls, runtime


def call(runtime, obj, *, channel='mt', pid=102):
    conn = Channel(obj, pid=pid)
    runtime.handle(channel, conn)
    return conn.result()


def worker(request='request', report='report', run='run'):
    return {'op':'worker_step', 'request_id':request, 'report_id':report, 'run_id':run}


def test_worker_and_canary_use_same_outbox_no_direct_git_client(shared):
    api, _, store, common, directory, calls, runtime = setup(shared)
    first = send(common, api.initial, mid='handoff')
    canary = call(runtime, {'op':'canary_report', 'head':first.commit_sha,
        'message_id':'adoption', 'body':'MT adoption report, no execution'})
    assert canary['ok']
    assert not (directory/'worker.sqlite').exists()
    task = send(common, canary['result']['commit'], mid='request', body=ACTION)
    try:
        result = call(runtime, worker())
        assert result['ok'] and result['result']['status'] == 'published'
        assert calls == [threading.get_ident()]
        for mid in ('handoff', 'adoption', 'request', 'report'):
            assert store.status(TASK, mid)['attempted']
            assert store.status(TASK, mid)['confirmed_commit']
        assert call(runtime, {'op':'writer_status','message_id':'report'})['result'] == store.status(TASK,'report')
        with sqlite3.connect(directory/'worker.sqlite') as conn:
            assert conn.execute('SELECT status,publication_attempted FROM executions').fetchone() == ('published',1)
    finally: runtime.close()


def test_worker_grant_and_append_grant_visible_before_network(shared):
    api, _, store, common, directory, _, runtime = setup(shared)
    send(common, api.initial, mid='request', body=ACTION)
    seen = []
    def hook(method, path, payload):
        if method != 'GET':
            with sqlite3.connect(directory/'worker.sqlite') as conn:
                assert conn.execute('SELECT status,publication_attempted FROM executions').fetchone() == ('result_ready',1)
            assert store.status(TASK,'report')['attempted'] is True
            seen.append(method)
    api.hook = hook
    try: assert call(runtime, worker())['ok']
    finally: runtime.close()
    assert seen == ['POST','POST','PATCH']


@pytest.mark.parametrize('effect', [False, True])
def test_worker_unknown_reopen_never_recomputes_or_resends(shared, monkeypatch, effect):
    api, _, _, common, directory, _, runtime = setup(shared)
    send(common, api.initial, mid='request', body=ACTION)
    original = api.request
    def lost(method,path,payload=None):
        if method == 'PATCH':
            if effect: original(method,path,payload)
            raise ApiError()
        return original(method,path,payload)
    api.request = lost
    assert not call(runtime, worker())['ok']
    runtime.close()
    monkeypatch.setattr('git_dialog.worker.evaluate', lambda *a: pytest.fail('must not recompute'))
    api.calls.clear()
    resumed = Runtime(common, bridge_pid=101,
        journal_factory=lambda: open_worker_journal(directory/'worker.sqlite'))
    try:
        assert call(resumed, worker())['ok'] is effect
        assert not writes(api)
    finally: resumed.close()


@pytest.mark.parametrize('extra', [{'actor':'chatgpt'}, {'task_id':TASK}, {'path':'elsewhere'},
    {'allowed_ops':['shell']}, {'callback':'eval'}, {'uid':1000}, {'writer':'worker'}])
def test_worker_extra_identity_policy_fields_fail_before_network(shared, extra):
    api, _, _, _, directory, calls, runtime = setup(shared)
    assert not call(runtime, {**worker(), **extra})['ok']
    assert not api.calls and not calls and not (directory/'worker.sqlite').exists()


@pytest.mark.parametrize('op', [worker(), {'op':'canary_report','head':'a'*40,'message_id':'r','body':'data'},
                                      {'op':'writer_status','message_id':'r'}])
def test_cg_cannot_invoke_mt_writer_capabilities(shared, op):
    api, _, _, _, _, calls, runtime = setup(shared)
    assert not call(runtime, op, channel='chatgpt', pid=101)['ok']
    assert not api.calls and not calls


@pytest.mark.parametrize('body', ['ordinary handoff prose',
    '{"protocol":"git-dialog-action","version":1,"op":"shell"}',
    '{"protocol":"git-dialog-action","version":1,"op":"ping","callback":"x"}'])
def test_worker_refuses_prose_and_untrusted_ops_without_journal_or_writes(shared, body):
    api, _, _, common, directory, calls, runtime = setup(shared)
    send(common, api.initial, mid='request', body=body)
    api.calls.clear()
    assert not call(runtime, worker())['ok']
    assert not calls and not (directory/'worker.sqlite').exists() and not writes(api)


@pytest.mark.parametrize('state', ['cancelled','closing','tombstoned'])
def test_lifecycle_blocks_worker_and_canary_before_network(shared, state):
    api, registry, _, common, _, calls, runtime = setup(shared)
    first = send(common, api.initial, mid='request', body=ACTION)
    if state == 'cancelled': registry.cancel(TASK)
    else:
        intent = registry.request_delete(TASK, first.commit_sha)
        if state == 'tombstoned': registry.confirm_delete(TASK, intent['confirmation'], first.commit_sha)
    api.calls.clear()
    assert not call(runtime, worker())['ok']
    assert not call(runtime, {'op':'canary_report','head':first.commit_sha,'message_id':'r','body':'data'})['ok']
    assert not api.calls and not calls


def test_journal_private_reopen_preserves_bytes_and_inode(tmp_path):
    path = tmp_path/'worker.sqlite'
    journal = open_worker_journal(path); journal.close()
    before, inode = path.read_bytes(), path.stat().st_ino
    journal = open_worker_journal(path); journal.close()
    assert path.read_bytes() == before and path.stat().st_ino == inode
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('kind', ['empty','foreign','symlink'])
def test_journal_never_adopts_foreign_file(tmp_path,kind):
    path = tmp_path/'worker.sqlite'
    if kind == 'symlink':
        target = tmp_path/'other'; target.touch(mode=0o600); path.symlink_to(target)
    else:
        path.touch(mode=0o600)
        if kind == 'foreign':
            with sqlite3.connect(path) as conn: conn.execute('CREATE TABLE private_data(x)')
    before = path.read_bytes()
    with pytest.raises(DialogError): open_worker_journal(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('action,op,obj', [
    ('worker','worker_step',{'request_id':'q','report_id':'r','run_id':'run'}),
    ('canary','canary_report',{'head':'a'*40,'message_id':'m','body':'inert'}),
    ('status','writer_status',{'message_id':'m'})])
def test_cli_one_call_no_path_credentials_sql_or_fallback(monkeypatch,capsys,action,op,obj):
    calls = []
    class Client:
        def __init__(self,path): assert path == cli.SOCKETS['mt']
        def call(self,request): calls.append(request); return {'status':'observed'}
    monkeypatch.setattr(cli,'UnixClient',Client)
    monkeypatch.setattr(cli.sys,'stdin',io.TextIOWrapper(io.BytesIO(json.dumps(obj).encode())))
    assert cli.main([action]) == 0
    assert calls == [{'op':op,**obj}]
    assert json.loads(capsys.readouterr().out) == {'status':'observed'}


@pytest.mark.parametrize('name,args', [('live_probe',['private','a'*40]),
    ('journal_probe',['prepare','private','db','a'*40]),
    ('worker_probe',['step','private','dir','a'*40])])
def test_legacy_cli_disabled_before_credential_read(monkeypatch,capsys,name,args):
    from importlib import import_module
    module = import_module('git_dialog.'+name)
    monkeypatch.setattr(module,'read_configured_token',lambda *a: pytest.fail('no credentials'))
    assert module.main(args) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'legacy_direct_writer_disabled_use_authority_writers'


@pytest.mark.parametrize('effect', [False, True])
def test_canary_unknown_does_not_resend(shared,effect):
    api, _, store, common, _, _, runtime = setup(shared)
    first = send(common, api.initial, mid='handoff')
    original = api.request
    def lost(method,path,payload=None):
        if method == 'PATCH':
            if effect: original(method,path,payload)
            raise ApiError()
        return original(method,path,payload)
    api.request = lost
    request = {'op':'canary_report','head':first.commit_sha,'message_id':'adoption','body':'inert'}
    assert not call(runtime,request)['ok']
    assert store.status(TASK,'adoption')['attempted']
    api.calls.clear()
    assert call(runtime,request)['ok'] is effect
    assert not writes(api)


def test_cli_unknown_is_one_attempt_only(monkeypatch,capsys):
    calls = []
    class Client:
        def __init__(self,path): pass
        def call(self,request):
            calls.append(request)
            raise RuntimeError('never expose private diagnostic')
    monkeypatch.setattr(cli,'UnixClient',Client)
    monkeypatch.setattr(cli.sys,'stdin',io.TextIOWrapper(io.BytesIO(b'{"request_id":"q","report_id":"r","run_id":"run"}')))
    assert cli.main(['worker']) == 1
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out) == {'error':'authority_result_unconfirmed','retry_authorized':False}


def test_started_worker_is_never_recomputed(shared,monkeypatch):
    api, _, _, common, directory, _, runtime = setup(shared)
    send(common,api.initial,mid='request',body=ACTION)
    journal = open_worker_journal(directory/'worker.sqlite')
    journal.receive(common.port('worker').load(TASK).last)
    assert journal.start(TASK,'request','run')
    journal.close()
    monkeypatch.setattr('git_dialog.worker.evaluate',lambda *a: pytest.fail('no recompute'))
    api.calls.clear()
    try: assert not call(runtime,worker())['ok']
    finally: runtime.close()
    assert not writes(api)


@pytest.mark.parametrize('body', [b'{"actor":"chatgpt"}', b'{"message_id":"x","message_id":"y"}', b'[]'])
def test_cli_extra_or_duplicate_fields_never_dispatch(monkeypatch,capsys,body):
    monkeypatch.setattr(cli,'UnixClient',lambda *a: pytest.fail('no dispatch'))
    monkeypatch.setattr(cli.sys,'stdin',io.TextIOWrapper(io.BytesIO(body)))
    assert cli.main(['status']) == 1
    assert json.loads(capsys.readouterr().out)['retry_authorized'] is False

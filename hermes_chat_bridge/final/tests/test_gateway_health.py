import json
import os
import stat
from types import SimpleNamespace

import pytest

from git_dialog import gateway_health as module


@pytest.fixture
def health(tmp_path, monkeypatch):
    store = tmp_path/'store.sqlite'; store.write_bytes(b'fixture'); store.chmod(0o600)
    monkeypatch.setattr(module,'APPENDS',store)
    def socket():
        return SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=stat.S_IFSOCK|0o600,
            st_uid=os.getuid(),st_nlink=1))
    monkeypatch.setattr(module,'SOCKETS',{'chatgpt':socket(),'mt':socket()})
    class Client:
        def __init__(self,path): assert path is module.SOCKETS['mt']
        def call(self,request):
            assert request == {'op':'view','offset':0,'limit':1}
            return {'head':'a'*40,'total_messages':6,'waiting_for':'chatgpt','body':'DO_NOT_FORWARD'}
    monkeypatch.setattr(module,'UnixClient',Client)
    return store


def test_one_read_only_health_returns_metadata_not_message_body(health,capsys):
    assert module.main() == 0
    raw = capsys.readouterr().out; value = json.loads(raw)
    assert 'DO_NOT_FORWARD' not in raw and str(health) not in raw
    assert value['status'] == 'gateway_live_viewed' and value['git_mutations'] == 0
    assert value['total_messages'] == 6 and value['append_store']['mode'] == '0600'


def test_bad_store_mode_is_not_repaired(health,capsys):
    health.chmod(0o644)
    assert module.main() == 1 and health.stat().st_mode & 0o777 == 0o644
    assert json.loads(capsys.readouterr().out)['error'] == 'gateway_health_unconfirmed'


def test_health_does_not_retry_or_forward_exception(health,monkeypatch,capsys):
    calls = []
    class Client:
        def __init__(self,path): pass
        def call(self,request): calls.append(request); raise RuntimeError('PRIVATE_DETAIL')
    monkeypatch.setattr(module,'UnixClient',Client)
    assert module.main() == 1 and len(calls) == 1
    assert 'PRIVATE_DETAIL' not in capsys.readouterr().out

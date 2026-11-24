"""Offline unit tests: no PostgreSQL credentials, no running services."""
import os
import pytest
pytest.importorskip('psycopg')
from bridge_pg.compat import Catalog,version_token,_checked_id,_checked_mid
from bridge_pg.store import BridgeError


def test_version_token_is_deterministic_and_changes():
    t='task_'+'1'*32
    a=version_token(t,0)
    assert len(a)==40 and all(c in '0123456789abcdef' for c in a)
    assert a==version_token(t,0)
    assert a!=version_token(t,1)


def test_validation():
    assert _checked_id('task_'+'a'*32)
    assert _checked_mid('report_abc-123')
    for value in ('foo','task_zzz','task_'+'a'*40):
        with pytest.raises(BridgeError):_checked_id(value)
    for value in ('bad/id','\n', ''):
        with pytest.raises(BridgeError):_checked_mid(value)


class FakeStore:
    def __init__(self):
        self.rows={}
    def create_dialog(self,request_id,title,initiator):
        key=(request_id,title,initiator)
        created=key not in self.rows
        self.rows.setdefault(key,dict(task_id='task_'+'a'*32,version=0,next_actor=initiator))
        return dict(self.rows[key],created=created)


def test_create_and_duplicate():
    c=Catalog(FakeStore())
    x=c.create('chatgpt','my_request','test')
    assert x['created'] is True
    assert x['waiting_for']=='chatgpt'
    assert c.create('chatgpt','my_request','test')['created'] is False
    assert x['head']==version_token(x['task_id'],0)

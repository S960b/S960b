"""P36 notice/index and local wake security regression tests. No live Git."""
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from git_dialog import DialogError
from git_dialog.dialog_catalog import DialogCatalog
from git_dialog.local_wake import notify_plugin

TASK = "task_" + "a"*32
H0, H1, H2 = "0"*40, "1"*40, "2"*40


def fixtures():
    a = NS(seq=1, message_id="m1", kind="task", sender="chatgpt",
           recipient="mt", body="long text")
    b = NS(seq=2, message_id="m2", kind="report", sender="mt",
           recipient="chatgpt", body="more long text")
    s1 = NS(state=NS(task_id=TASK,seq=1,status="working",next_actor="mt"),
            head=H1,messages=[a],last=a)
    s2 = NS(state=NS(task_id=TASK,seq=2,status="working",next_actor="chatgpt"),
            head=H2,messages=[a,b],last=b)
    class Dialog:
        def __init__(self): self.calls = []
        def load(self,t):
            assert t==TASK
            return s2
        def _snapshot(self,t,h):
            assert t==TASK
            return ((s1 if h==H1 else s2),None,None)
        def _commit(self,h):
            self.calls.append(h)
            return {"parents":[{"sha":H1}] if h==H2 else [{"sha":H0}]}
    dlg=Dialog()
    reg=NS(get=lambda t:{"state":"active"})
    obj=NS(dialog=dlg,registry=reg,_common=lambda t: NS(gateway=NS(load=lambda _:s2)))
    return obj,dlg


def test_notice_verifies_exact_chatgpt_message_ancestor_without_body():
    obj,d=fixtures()
    output=DialogCatalog.notice(obj,"chatgpt",TASK,H1,"m1",1)
    assert output=={"task_id":TASK,"head":H1,"message_id":"m1",
                    "seq":1,"observed":True}
    assert d.calls==[H2]
    assert "body" not in json.dumps(output)


def test_notice_rejects_wrong_sender_or_wrong_id():
    obj,_=fixtures()
    with pytest.raises(DialogError,match="direct_notice_unconfirmed"):
        DialogCatalog.notice(obj,"mt",TASK,H1,"m1",1)
    with pytest.raises(DialogError,match="direct_notice_unconfirmed"):
        DialogCatalog.notice(obj,"chatgpt",TASK,H1,"m2",1)


def test_index_only_exposes_ids():
    obj,_=fixtures()
    result=DialogCatalog.index(obj,TASK,limit=10)
    assert result["head"]==H2
    assert result["messages"][0]["message_id"]=="m2"
    assert "long text" not in json.dumps(result)
    assert "body" not in json.dumps(result)


def test_local_wake_never_writes_body(tmp_path):
    tmp_path.chmod(0o700)
    receipt={"task_id":TASK,"head":H1,"message_id":"m1",
             "seq":1,"observed":True}
    assert notify_plugin(receipt,tmp_path) is True
    raw=(tmp_path/"p36-notice.json").read_text()
    assert json.loads(raw)=={"task_id":TASK,"head":H1,"message_id":"m1","seq":1}
    assert "body" not in raw
    assert not list(tmp_path.glob(".p36-notice-*"))


def test_local_wake_fails_closed_on_unsafe_directory(tmp_path):
    tmp_path.chmod(0o755)
    receipt={"task_id":TASK,"head":H1,"message_id":"m1",
             "seq":1,"observed":True}
    assert notify_plugin(receipt,tmp_path) is False
    assert not (tmp_path/"p36-notice.json").exists()

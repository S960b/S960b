import io
import json
import re
from urllib.parse import urlencode

import pytest

from git_dialog.panel_gateway import GatewayPanel
from git_dialog.gateway import panel_view
from test_shared_authority import shared,send
from test_github import TASK


class Auth:
    def verify(self,value): return value=='accepted-owner'


def setup(shared):
    api,_,store,common,_=shared
    calls=[]
    class Client:
        def call(self,request):
            calls.append(request)
            if request['op']=='view':
                return panel_view(common.port('mt').load(TASK),offset=request['offset'],limit=request['limit'])
            value=common.port('mt').append(TASK,request['head'],message_id=request['message_id'],
                actor='mt',kind=request['kind'],body=request['body'])
            return {'message_id':request['message_id'],'commit':value.commit_sha,'created':value.created}
    panel=GatewayPanel(Auth(),8791,client=Client())
    return api,store,common,calls,panel


def call(panel,method='GET',path='/',body=None,**extra):
    raw=b'' if body is None else urlencode(body).encode()
    env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','REMOTE_ADDR':'127.0.0.1',
        'HTTP_HOST':panel.host,'HTTP_AUTHORIZATION':'accepted-owner','CONTENT_LENGTH':str(len(raw)),
        'CONTENT_TYPE':'application/x-www-form-urlencoded','wsgi.input':io.BytesIO(raw)}
    if method=='POST': env['HTTP_ORIGIN']=panel.origin
    env.update(extra); received=[]
    result=b''.join(panel(env,lambda s,h:received.append((int(s.split()[0]),dict(h)))))
    return *received[0],result


def token(panel):
    code,_,body=call(panel)
    assert code==200
    return re.search(b'name="csrf" value="([^"]+)"',body)[1].decode()


def test_owner_form_appends_through_same_authority_and_store(shared):
    api,store,common,calls,panel=setup(shared)
    send(common,api.initial,mid='task')
    csrf=token(panel)
    code,headers,body=call(panel,'POST','/send',{'csrf':csrf,'body':'owner text <script>not code</script>'})
    result=json.loads(body)
    assert code==200 and result['status']=='confirmed'
    assert store.status(TASK,result['message_id'])['confirmed_commit']==result['commit']
    assert calls[-1]['kind']=='report' and 'actor' not in calls[-1]
    assert headers['Cache-Control']=='no-store'
    count=len(calls)
    assert call(panel,'POST','/send',{'csrf':csrf,'body':'owner text <script>not code</script>'})[0]==403
    assert len(calls)==count
    code,_,body=call(panel)
    assert code==200 and b'&lt;script&gt;not code&lt;/script&gt;' in body
    assert b'<form ' not in body  # now CG turn


@pytest.mark.parametrize('extra',[{'HTTP_AUTHORIZATION':None},{'HTTP_AUTHORIZATION':'wrong'},
    {'REMOTE_ADDR':'8.8.8.8'},{'HTTP_HOST':'evil'},{'HTTP_ORIGIN':'http://evil'},
    {'HTTP_X_FORWARDED_FOR':'127.0.0.1'}])
def test_boundary_denied_before_gateway(shared,extra):
    *_,calls,panel=setup(shared)
    assert call(panel,**extra)[0] in (401,403)
    assert not calls


@pytest.mark.parametrize('extra',[{'HTTP_ORIGIN':None},{'HTTP_ORIGIN':'http://evil'},
    {'HTTP_TRANSFER_ENCODING':'chunked'},{'HTTP_EXPECT':'100-continue'},
    {'QUERY_STRING':'x=1'},{'CONTENT_TYPE':'application/json'},{'CONTENT_LENGTH':'9999'}])
def test_post_fails_before_dispatch(shared,extra):
    *_,calls,panel=setup(shared)
    assert call(panel,'POST','/send',{'csrf':'wrong','body':'data'},**extra)[0]==403
    assert not calls


def test_unknown_never_retries_and_blocks_new_forms(shared):
    api,_,common,calls,panel=setup(shared)
    send(common,api.initial,mid='task')
    csrf=token(panel)
    def lost(request): calls.append(request); raise RuntimeError('private')
    panel.client.call=lost
    code,_,body=call(panel,'POST','/send',{'csrf':csrf,'body':'exact intent'})
    result=json.loads(body)
    assert code==503 and result['retry_authorized'] is False
    n=len(calls)
    assert call(panel,'POST','/send',{'csrf':csrf,'body':'exact intent'})[0]==403
    assert len(calls)==n and panel.uncertain
    assert '<form ' not in panel._form(api.initial)


def test_restart_same_head_body_has_same_message_id(shared):
    api,_,common,calls,panel=setup(shared)
    send(common,api.initial,mid='task')
    first=token(panel)
    def lost(request): calls.append(request); raise RuntimeError()
    panel.client.call=lost
    a=json.loads(call(panel,'POST','/send',{'csrf':first,'body':'same'})[2])
    _,_,_,_,other=setup(shared)
    other.client.call=lambda r: {'head':calls[-1]['head'],'waiting_for':'mt'}  # form binding only
    second=other._form(calls[-1]['head'])
    csrf=re.search('name="csrf" value="([^"]+)"',second)[1]
    other.client.call=lost
    b=json.loads(call(other,'POST','/send',{'csrf':csrf,'body':'same'})[2])
    assert a['message_id']==b['message_id']


@pytest.mark.parametrize('body',['','  ','x'*2049])
def test_invalid_body_not_consumed_or_dispatched(shared,body):
    api,_,common,calls,panel=setup(shared)
    send(common,api.initial,mid='task'); csrf=token(panel); n=len(calls)
    assert call(panel,'POST','/send',{'csrf':csrf,'body':body})[0]==403
    assert len(calls)==n


def test_status_honest_fixed_mt_no_delete(shared):
    *_,panel=setup(shared)
    code,_,raw=call(panel,path='/api/status'); status=json.loads(raw)
    assert code==200 and status['writer_actor']=='mt' and not status['read_only']
    assert not status['delete_enabled']


def test_existing_panel_stays_read_only_by_default(shared):
    from git_dialog.service import PanelApplication,SnapshotReader
    panel=PanelApplication(Auth(),SnapshotReader(),8791)
    assert panel.gateway is None


def test_expired_form_and_capacity_fail_closed(shared):
    api,_,common,calls,panel=setup(shared)
    send(common,api.initial,mid='task'); csrf=token(panel)
    panel.forms[csrf]['expires']=0
    assert call(panel,'POST','/send',{'csrf':csrf,'body':'data'})[0]==403
    for i in range(64): panel._form(api.initial)
    assert '<form ' not in panel._form(api.initial)

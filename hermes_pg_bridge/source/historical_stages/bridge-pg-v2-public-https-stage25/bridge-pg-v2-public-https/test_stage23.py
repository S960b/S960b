"""Offline safety and edge readiness tests. No network, systemd, or DB use."""
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest import mock

p=Path(__file__).parent/'23_public_https.py'
spec=importlib.util.spec_from_file_location('pgv2_https_stage',p)
s=importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

class R:
    def __init__(self,status=200,headers=None,obj=None):
        self.status=status
        self.headers=headers or {}
        self.body=json.dumps(obj or {}).encode()

def test_public_verify():
    base='https://coastal-wolf-stellar.trycloudflare.com'
    urls={
      '/ping':R(obj={'service':'pg-bridge-v2'}),
      '/mcp':R(401,{'WWW-Authenticate':f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'}),
      '/.well-known/oauth-authorization-server':R(obj={'issuer':base, **{x:base+'/'+x for x in ('authorization_endpoint','token_endpoint','registration_endpoint')}}),
      '/.well-known/oauth-protected-resource/mcp':R(obj={'resource':base+'/mcp'}),
    }
    statuses=[530,404,200]
    def f(url,**kw):
        for k,v in urls.items():
            if url==base+k:
                if k=='/ping':
                    status=statuses.pop(0) if statuses else 200
                    return (status,v.headers,v.body if status==200 else b'')
                return v.status,v.headers,v.body
        raise AssertionError(url)
    with mock.patch.object(s,'fetch',side_effect=f),mock.patch.object(s,'active_tunnel_pid',return_value=909),mock.patch.object(s.time,'sleep'):
        s.verify_public(base,909)
    urls['/mcp']=R(200)
    with mock.patch.object(s,'fetch',side_effect=f),mock.patch.object(s,'active_tunnel_pid',return_value=909),mock.patch.object(s.time,'sleep'):
        try:s.verify_public(base,909)
        except RuntimeError as e:assert str(e)=='public_mcp_unauthorized_not_blocked'
        else:raise AssertionError('permissive public MCP accepted')
    with mock.patch.object(s,'fetch',side_effect=f),mock.patch.object(s,'active_tunnel_pid',return_value=908):
        try:s.verify_public(base,909)
        except RuntimeError as e:assert str(e)=='tunnel_process_changed_during_public_check'
        else:raise AssertionError('restarted tunnel was allowed')

def test_journal_pid_binding():
    old='https://old-url.trycloudflare.com'
    new='https://new-url.trycloudflare.com'
    def entry(pid,text):return json.dumps({'_PID':str(pid),'MESSAGE':text})
    logs='\n'.join([
        entry(100,f'Quick Tunnel URL: {old}'),
        entry(100,'Registered tunnel connection connIndex=0 protocol=http2'),
        entry(200,f'Quick Tunnel URL: {new}'),
        entry(200,'Registered tunnel connection connIndex=0 protocol=http2'),
    ])
    assert s.current_process_tunnel_url(logs,200)==new
    assert s.current_process_tunnel_url(logs,100)==old
    assert s.current_process_tunnel_url(logs,300) is None
    incomplete=entry(200,f'URL: {new}')
    assert s.current_process_tunnel_url(incomplete,200) is None
    wrong_protocol=incomplete+'\n'+entry(200,'Registered tunnel connection connIndex=0 protocol=quic')
    assert s.current_process_tunnel_url(wrong_protocol,200) is None
    # Regression: dead PID has a URL and edge registration; live process hasn't registered.
    mixed='\n'.join([entry(100,f'URL: {old}'),entry(100,'Registered tunnel connection protocol=http2'),entry(200,f'URL: {new}')])
    assert s.current_process_tunnel_url(mixed,200) is None

def test_private_write():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'secret'
        s.private_write(p,b'hello')
        assert p.read_bytes()==b'hello'
        assert p.stat().st_mode & 0o077 == 0
        try:s.private_write(p,b'evil')
        except RuntimeError as e:assert 'refuse_overwrite' in str(e)
        else:raise AssertionError('overwrite was allowed')

def test_transport():
    import inspect
    src=inspect.getsource(s.start_tunnel)
    assert src.count('--protocol http2 --url http://127.0.0.1:8766')==1
    assert 'Restart=no' in src
    assert 'Wants=bridge-pg-v2-mcp.service' in src
    assert 'Requires=bridge-pg-v2-mcp.service' not in src
    assert 'After=network.target bridge-pg-v2-mcp.service' in src
    assert 'current_process_tunnel_url' in inspect.getsource(s.wait_for_live_tunnel)
    assert '120' in inspect.signature(s.verify_public).parameters['budget_s'].default.__str__()

def main():
    test_transport()
    test_journal_pid_binding()
    test_public_verify()
    test_private_write()
    print('STAGE23_OFFLINE_SAFETY_TESTS_OK')
    print('STAGE24_PID_BOUND_HTTP2_EDGE_READY_TESTS_OK')
    print('STAGE25_SYSTEMD_DEPENDENCY_NON_PROPAGATING_OK')
if __name__=='__main__':main()

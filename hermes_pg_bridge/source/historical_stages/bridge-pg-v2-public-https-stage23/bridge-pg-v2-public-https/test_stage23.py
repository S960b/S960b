"""Offline guardrail tests: no shell/network/systemd side effects."""
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest import mock

p=Path(__file__).parent/'23_public_https.py'
spec=importlib.util.spec_from_file_location('pgv2_https_stage', p)
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
    def f(url,**kw):
        for k,v in urls.items():
            if url==base+k:return v.status,v.headers,v.body
        raise AssertionError(url)
    with mock.patch.object(s,'fetch',side_effect=f):
        s.verify_public(base)
    urls['/mcp']=R(200)
    with mock.patch.object(s,'fetch',side_effect=f):
        try:s.verify_public(base)
        except RuntimeError as e:assert str(e)=='public_mcp_unauthorized_not_blocked'
        else:raise AssertionError('permissive public MCP accepted')

def test_private_write():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'secret'
        s.private_write(p,b'hello')
        assert p.read_bytes()==b'hello'
        assert p.stat().st_mode & 0o077 == 0
        try:s.private_write(p,b'evil')
        except RuntimeError as e:assert 'refuse_overwrite' in str(e)
        else:raise AssertionError('overwrite was allowed')

def main():
    test_public_verify()
    test_private_write()
    print('STAGE23_OFFLINE_SAFETY_TESTS_OK')
if __name__=='__main__':main()

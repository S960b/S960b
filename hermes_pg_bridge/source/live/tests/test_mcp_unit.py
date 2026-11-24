import asyncio
import pytest
pytest.importorskip('psycopg')
from bridge_pg.mcp_v2 import register_mcp

class FakeMCP:
    def __init__(self):self.tools={}
    def tool(self,**kw):
        def decorate(f):self.tools[f.__name__]=f;return f
        return decorate
class FakeBridge:
    def __init__(self):self.authorized=True
    def require_owner(self):
        if not self.authorized:raise PermissionError('no verified OAuth principal')
        return 'owner'
class FakeCatalog:
    def list(self,offset,limit):return {'dialogs':[], 'next_offset':None, 'order':'newest'}

def test_owner_guard_and_nine_tools():
    bridge=FakeBridge();mcp=FakeMCP()
    assert len(register_mcp(mcp,bridge,catalog=FakeCatalog()))==9
    assert 'dialog_view' in mcp.tools
    assert asyncio.run(mcp.tools['dialog_list']())['dialogs']==[]
    bridge.authorized=False
    with pytest.raises(PermissionError):asyncio.run(mcp.tools['dialog_list']())

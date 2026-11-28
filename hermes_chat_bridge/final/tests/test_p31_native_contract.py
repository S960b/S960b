"""P31: native MCP annotations and explicit task selection regression."""
import asyncio
import inspect

from git_dialog import native_gateway


def annotation_dict(value):
    return value if isinstance(value, dict) else value.model_dump(by_alias=True, exclude_none=True)


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def register(func):
            self.tools[func.__name__] = (func, kwargs)
            return func
        return register


class FakeBridge:
    def require_owner(self):
        return "owner"


def test_native_tools_have_read_write_annotations(monkeypatch):
    # Exact-client binding retained: use the real client type but prevent I/O.
    monkeypatch.setattr(native_gateway.UnixClient, "call", lambda self, req: req)
    mcp = FakeMCP()
    native_gateway.register_mcp(mcp, FakeBridge())
    assert set(mcp.tools) == {
        "dialog_create", "dialog_creation_status", "dialog_list",
        "dialog_index", "dialog_health", "dialog_notice",
    }
    for name in ("dialog_list", "dialog_index", "dialog_creation_status", "dialog_health"):
        ann = annotation_dict(mcp.tools[name][1]["annotations"])
        assert ann["readOnlyHint"] is True
        assert ann["destructiveHint"] is False
    for name in ("dialog_notice", "dialog_create"):
        # SDK 2.3 returns a pydantic ToolAnnotations object, not a dict:
        # normalise before asserting so the test works across SDK versions.
        ann = annotation_dict(mcp.tools[name][1]["annotations"])
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
    for func, options in mcp.tools.values():
        assert options["meta"]["securitySchemes"]
        assert inspect.iscoroutinefunction(func)


def test_index_and_notice_forward_explicit_task_id(monkeypatch):
    monkeypatch.setattr(native_gateway.UnixClient, "call", lambda self, req: req)
    mcp = FakeMCP()
    native_gateway.register_mcp(mcp, FakeBridge())
    view = mcp.tools["dialog_index"][0]
    health = mcp.tools["dialog_health"][0]
    notice = mcp.tools["dialog_notice"][0]
    task = "task_" + "a" * 32
    assert asyncio.run(view(task, offset=0, limit=2))["task_id"] == task
    assert asyncio.run(health(task)) == {'op': 'health', 'task_id': task}
    result = asyncio.run(notice(task, "b" * 40, "p31_test_message", 7))
    assert result["task_id"] == task
    assert result["message_id"] == "p31_test_message"
    assert result["seq"] == 7
    assert "body" not in result

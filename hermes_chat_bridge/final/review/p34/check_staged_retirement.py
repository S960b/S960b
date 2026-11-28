#!/usr/bin/env python3
"""Offline verification of the staged P34 bridge. No live endpoints."""
import argparse
import ast
import asyncio
import importlib.util
import inspect
from pathlib import Path
import sys
import tempfile

NAMES = {"dialog_create", "dialog_creation_status", "dialog_list",
         "dialog_view", "dialog_health", "dialog_append"}
REMOVED = {"bridge_get_message", "bridge_put_reply", "bridge_put_message",
           "bridge_subscribe", "bridge_subscription_status", "bridge_unsubscribe"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("staged_file", type=Path)
    parser.add_argument("git_dialog_src", type=Path)
    args = parser.parse_args()
    staged = args.staged_file.resolve()
    source = staged.read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "make_mcp_server")
    functions = {n.name for n in factory.body if isinstance(n, ast.AsyncFunctionDef)}
    assert not (functions & REMOVED), "legacy functions still registered"
    assert "register_mcp(mcp, bridge)" in source
    assert "install_gateway_lifespan(app, bridge" in source
    assert not any("add_request_handler('events/" in s for s in source.splitlines())
    sys.path[:0] = [str(args.git_dialog_src.resolve()), str(staged.parent)]
    spec = importlib.util.spec_from_file_location("bridge_server", staged)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bridge_server"] = module
    spec.loader.exec_module(module)
    from git_dialog.native_gateway import register_mcp
    with tempfile.TemporaryDirectory(prefix="p34-check-") as tmp:
        module.DB_PATH = str(Path(tmp) / "test.db")
        module.OWNER_PASS = "test-only-value"
        bridge = module.BridgeApp("https://bridge.example")
        try:
            server = module.make_mcp_server("https://bridge.example", bridge)
            assert set(register_mcp(server, bridge)) == NAMES
            tool_list = server._tool_manager.list_tools()
            if inspect.isawaitable(tool_list):
                tool_list = asyncio.run(tool_list)
            names = {tool.name for tool in tool_list}
            assert names == NAMES, f"expected six dialogs; received: {sorted(names)}"
            assert module.jsonable_discover()["capabilities"] == {"tools": {}}
            print("P34_OFFLINE_PASS", ",".join(sorted(names)))
        finally:
            bridge.queue._conn.close()
            bridge.store.conn.close()

if __name__ == "__main__":
    main()

"""PostgreSQL persistence kernel for Hermes/ChatGPT Bridge v2.

This library intentionally has no GitHub API, HTTP, MCP, or MultiTool imports.
MCP compatibility adapters and the taskboard will be added after the live
interface inventory. The DB functions—not untrusted SQL—enforce atomicity.
"""
from .store import Store, BridgeError

__all__ = ['Store', 'BridgeError']

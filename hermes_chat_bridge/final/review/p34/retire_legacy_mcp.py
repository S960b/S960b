#!/usr/bin/env python3
"""P34: retire legacy Hermes MCP registrations in a STAGED COPY only.

Works only with the owner-audited live bridge_server.py SHA256. Never edits
the input file, OAuth, local registry, databases, services or Git references.
"""
import argparse
import ast
import hashlib
from pathlib import Path
import re

EXPECTED_SHA256 = "48f3a710904269b1f0f335a98c0e151e509fb0c39d661a2fdf2e0aef93e846e9"
LEGACY = {
    "bridge_get_message", "bridge_put_reply", "bridge_put_message",
    "bridge_subscribe", "bridge_subscription_status", "bridge_unsubscribe",
}
EVENT_HANDLERS = {"_ev_list", "_ev_subscribe", "_ev_unsubscribe"}
EVENT_METHODS = ("events/list", "events/subscribe", "events/unsubscribe")


def transform(source):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    make = [n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name == "make_mcp_server"]
    discover = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "jsonable_discover"]
    if len(make) != 1 or len(discover) != 1:
        raise ValueError("MCP factory/discovery layout changed")
    make, discover = make[0], discover[0]
    edits = []
    defs = {n.name: n for n in make.body if isinstance(n, ast.AsyncFunctionDef)}
    if not LEGACY.issubset(defs) or not EVENT_HANDLERS.issubset(defs):
        raise ValueError("old handler inventory differs from audit")

    for name in sorted(LEGACY | EVENT_HANDLERS):
        node = defs[name]
        start = min((d.lineno for d in node.decorator_list), default=node.lineno)
        if name in LEGACY and not any("@mcp.tool(" in lines[i - 1]
                                         for i in range(start, node.lineno)):
            raise ValueError("missing MCP tool decorator for " + name)
        edits.append((start, node.end_lineno, ""))

    for method in EVENT_METHODS:
        found = [i for i, line in enumerate(lines, 1)
                 if make.lineno <= i <= make.end_lineno
                 and re.search(r"^\s*mcp\._lowlevel_server\.add_request_handler\(['\"]"
                               + re.escape(method) + r"['\"]", line)]
        if len(found) != 1:
            raise ValueError("event registration changed: " + method)
        edits.append((found[0], found[0], ""))

    compat = [i for i, line in enumerate(lines, 1)
              if make.lineno <= i <= make.end_lineno
              and re.fullmatch(r"\s*install_discover_events_compat\(mcp\)\s*", line)]
    if len(compat) != 1:
        raise ValueError("events-compat hook changed")
    edits.append((compat[0], compat[0], ""))

    constructors = [n for n in make.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "mcp" for t in n.targets)
                    and isinstance(n.value, ast.Call)]
    if len(constructors) != 1:
        raise ValueError("MCPServer constructor changed")
    kw = [k for k in constructors[0].value.keywords if k.arg == "instructions"]
    if len(kw) != 1:
        raise ValueError("MCPServer instructions not found")
    value = kw[0].value
    old = "".join(lines[value.lineno - 1:value.end_lineno])
    if "bridge_get_message" not in old or "bridge_put_reply" not in old:
        raise ValueError("legacy instructions changed")
    edits.append((value.lineno, value.end_lineno,
                  "        instructions=('GitHub-диалоги ChatGPT и MT. Используйте только dialog_*.'),\n"))

    dl = range(discover.lineno, discover.end_lineno + 1)
    caps = [i for i in dl if "'capabilities': {'tools': {}, 'events': {}}" in lines[i - 1]]
    hint = [i for i in dl if "'instructions': 'Очередь сообщений Hermes<->ChatGPT" in lines[i - 1]]
    if len(caps) != 1 or len(hint) != 1:
        raise ValueError("discovery metadata changed")
    edits.append((caps[0], caps[0], lines[caps[0] - 1].replace(
        "'capabilities': {'tools': {}, 'events': {}}", "'capabilities': {'tools': {}}")))
    edits.append((hint[0], hint[0],
                  "        'instructions': 'GitHub-диалоги ChatGPT и MT через dialog_*.',\n"))

    edits.sort(reverse=True)
    for idx, (first, last, replacement) in enumerate(edits):
        if not 1 <= first <= last <= len(lines):
            raise ValueError("source span invalid")
        if idx and last >= edits[idx - 1][0]:
            raise ValueError("overlapping edits")
        lines[first - 1:last] = [replacement] if replacement else []
    result = "".join(lines)
    ast.parse(result)

    # Byte-for-byte preservation of these audited P32/P33 integration markers.
    for marker in (
        "register_mcp(mcp, bridge)",
        "install_gateway_lifespan(app, bridge",
        "bridge.configure_beta_wakeup()",
        "self._beta_forwarder.offer(payload)",
        "mcp._lowlevel_server.add_request_handler('server/discover'",
        "auth_server_provider=bridge.provider",
    ):
        if marker not in result or result.count(marker) != source.count(marker):
            raise ValueError("protected integration changed: " + marker)
    new_tree = ast.parse(result)
    new_factory = next(n for n in new_tree.body if isinstance(n, ast.FunctionDef)
                       and n.name == "make_mcp_server")
    names = {n.name for n in new_factory.body if isinstance(n, ast.AsyncFunctionDef)}
    if names & (LEGACY | EVENT_HANDLERS):
        raise ValueError("old handlers remain")
    if any("add_request_handler('events/" in l for l in result.splitlines()):
        raise ValueError("old events protocol methods remain")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="copy of exact live 1299-line source")
    parser.add_argument("output", type=Path, help="new staged path, never the live path")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        parser.error("source SHA256 mismatch: " + digest)
    if args.input.resolve() == args.output.resolve():
        parser.error("refusing to overwrite source")
    if args.output.exists() or args.output.is_symlink():
        parser.error("output already exists")
    staged = transform(raw.decode("utf-8"))
    with args.output.open("x", encoding="utf-8", newline="") as f:
        f.write(staged)
    print("P34_STAGED_OK")
    print("source_sha256=" + digest)
    print("staged_sha256=" + hashlib.sha256(staged.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    main()

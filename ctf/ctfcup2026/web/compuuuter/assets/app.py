#!/usr/bin/env python3
"""
baby-compuuuter -- a tiny in-memory "graph computer".

You build a dataflow graph out of numeric nodes. Each node has a numeric
``id``, a numeric ``type`` (the operation) and a list of ``args`` which are the
ids of the nodes whose outputs it consumes. You upload a graph, patch it,
compute it and read back the per-node results.

There is a hidden opcode, type 1337, whose output is the flag (the flag bytes as
a little-endian integer). Normal users are not allowed to use it: the very first
thing ``compute`` does is check that no node has type 1337. Only *after* that
check passes does it build its node table and evaluate. What could go wrong?
"""

import os
import sys
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")


# --------------------------------------------------------------------------- #
#  Flag
# --------------------------------------------------------------------------- #
def _load_flag() -> str:
    env = os.environ.get("FLAG")
    if env:
        return env.strip()
    try:
        with open("/flag.txt", "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "ctfcup{local_test_flag_placeholder}"


FLAG = _load_flag()
# The flag node outputs the flag bytes interpreted as a little-endian integer.
FLAG_INT = int.from_bytes(FLAG.encode(), "little")

# --------------------------------------------------------------------------- #
#  Operation table (numeric type -> callable)
# --------------------------------------------------------------------------- #
T_CONST, T_ADD, T_SUB, T_MUL, T_XOR = 1, 2, 3, 4, 5
T_AND, T_OR, T_SHL, T_SHR, T_MOD = 6, 7, 8, 9, 10
T_FLAG = 1337  # the hidden opcode whose output is the flag

OPS = {
    T_ADD: lambda a: sum(a),
    T_SUB: lambda a: a[0] - sum(a[1:]),
    T_MUL: lambda a: _product(a),
    T_XOR: lambda a: _reduce_xor(a),
    T_AND: lambda a: _reduce(lambda x, y: x & y, a, -1),
    T_OR: lambda a: _reduce(lambda x, y: x | y, a, 0),
    T_SHL: lambda a: a[0] << (a[1] if len(a) > 1 else 0),
    T_SHR: lambda a: a[0] >> (a[1] if len(a) > 1 else 0),
    T_MOD: lambda a: a[0] % a[1],
}

OP_NAMES = {
    T_CONST: "const", T_ADD: "add", T_SUB: "sub", T_MUL: "mul", T_XOR: "xor",
    T_AND: "and", T_OR: "or", T_SHL: "shl", T_SHR: "shr", T_MOD: "mod",
}


def _product(a):
    out = 1
    for x in a:
        out *= x
    return out


def _reduce_xor(a):
    out = 0
    for x in a:
        out ^= x
    return out


def _reduce(fn, a, empty):
    if not a:
        return empty
    out = a[0]
    for x in a[1:]:
        out = fn(out, x)
    return out


# --------------------------------------------------------------------------- #
#  Graph store (everything in memory)
# --------------------------------------------------------------------------- #
GRAPHS = {}           # gid -> {"nodes": [...], "results": {...} | None}
GRAPHS_LOCK = threading.Lock()
MAX_NODES = 100_000


class GraphError(Exception):
    pass


def _as_int(value, what):
    # JSON ints arrive as Python ints; reject floats / bools / strings.
    if isinstance(value, bool) or not isinstance(value, int):
        raise GraphError(f"{what} must be an integer")
    return value


def parse_nodes(raw):
    if not isinstance(raw, list):
        raise GraphError("`nodes` must be a list")
    if len(raw) > MAX_NODES:
        raise GraphError(f"too many nodes (max {MAX_NODES})")
    nodes = []
    seen = set()
    for item in raw:
        nodes.append(parse_node(item, seen))
    return nodes


def parse_node(item, seen=None):
    if not isinstance(item, dict):
        raise GraphError("each node must be an object")
    nid = _as_int(item.get("id"), "node id")
    ntype = _as_int(item.get("type"), "node type")
    args = item.get("args", [])
    if not isinstance(args, list):
        raise GraphError("`args` must be a list")
    args = [_as_int(a, "arg") for a in args]
    node = {"id": nid, "type": ntype, "args": args}
    if ntype == T_CONST:
        node["value"] = _as_int(item.get("value", 0), "const value")
    if seen is not None:
        if nid in seen:
            raise GraphError(f"duplicate node id {nid}")
        seen.add(nid)
    return node


def guard(nodes):
    """Make sure nobody sneaks in the flag opcode (type 1337)."""
    for node in nodes:
        if node["type"] == T_FLAG:
            raise GraphError("the flag operation (type 1337) is not available")


def compute(nodes):
    # TIME OF CHECK: the flag opcode must be absent.
    guard(nodes)

    # Index nodes by their id. For a well-behaved graph this is linear; it is
    # the heavy part of a run.
    nmap = {}
    for node in nodes:
        nmap[node["id"]] = node

    memo = {}

    def evaluate(nid, stack):
        node = nmap.get(nid)
        if node is None:
            raise GraphError(f"node {nid} does not exist")
        key = node["id"]
        if key in memo:
            return memo[key]
        if key in stack:
            raise GraphError("cycle detected in graph")
        stack = stack | {key}

        # TIME OF USE: node fields are read here, live.
        t = node["type"]
        if t == T_FLAG:
            val = FLAG_INT
        elif t == T_CONST:
            val = node.get("value", 0)
        else:
            op = OPS.get(t)
            if op is None:
                raise GraphError(f"unknown operation {t}")
            argvals = [evaluate(a, stack) for a in node["args"]]
            try:
                val = op(argvals)
            except (IndexError, ZeroDivisionError) as exc:
                raise GraphError(f"node {key}: {exc}") from exc
        memo[key] = val
        return val

    results = {}
    for node in nodes:
        results[str(node["id"])] = str(evaluate(node["id"], frozenset()))
    return results


# --------------------------------------------------------------------------- #
#  HTTP API
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/graph")
def upload_graph():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="invalid JSON body"), 400
    try:
        nodes = parse_nodes(body.get("nodes"))
        guard(nodes)  # reject graphs that use the flag opcode at upload time too
    except GraphError as exc:
        return jsonify(error=str(exc)), 400
    gid = uuid.uuid4().hex
    with GRAPHS_LOCK:
        GRAPHS[gid] = {"nodes": nodes, "results": None}
    return jsonify(id=gid, node_count=len(nodes))


@app.get("/api/graph/<gid>")
def get_graph(gid):
    graph = GRAPHS.get(gid)
    if graph is None:
        return jsonify(error="no such graph"), 404
    return jsonify(id=gid, node_count=len(graph["nodes"]), results=graph["results"])


@app.patch("/api/graph/<gid>")
def patch_graph(gid):
    graph = GRAPHS.get(gid)
    if graph is None:
        return jsonify(error="no such graph"), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="invalid JSON body"), 400
    try:
        patch = parse_node(body)  # a quick edit -- no flag-opcode check here
    except GraphError as exc:
        return jsonify(error=str(exc)), 400

    nodes = graph["nodes"]
    for node in nodes:
        if node["id"] == patch["id"]:
            # Edit the existing node in place (keep the same object & id).
            node["type"] = patch["type"]
            node["args"] = patch["args"]
            if "value" in patch:
                node["value"] = patch["value"]
            break
    else:
        if len(nodes) >= MAX_NODES:
            return jsonify(error=f"too many nodes (max {MAX_NODES})"), 400
        nodes.append(patch)
    return jsonify(id=gid, ok=True)


@app.post("/api/graph/<gid>/compute")
def compute_graph(gid):
    graph = GRAPHS.get(gid)
    if graph is None:
        return jsonify(error="no such graph"), 404
    try:
        results = compute(graph["nodes"])
    except GraphError as exc:
        return jsonify(error=str(exc)), 400
    graph["results"] = results
    return jsonify(id=gid, results=results)


@app.get("/api/graph/<gid>/result")
def graph_result(gid):
    graph = GRAPHS.get(gid)
    if graph is None:
        return jsonify(error="no such graph"), 404
    if graph["results"] is None:
        return jsonify(error="graph has not been computed yet"), 409
    return jsonify(id=gid, results=graph["results"])


@app.get("/api/ops")
def list_ops():
    return jsonify(ops=OP_NAMES)


if __name__ == "__main__":
    # On 64-bit CPython, hash(n) == n % (2**61 - 1) for non-negative ints.
    assert sys.hash_info.modulus == 2 ** 61 - 1, "run me on 64-bit CPython"
    app.run(host="0.0.0.0", port=3000, threaded=True)

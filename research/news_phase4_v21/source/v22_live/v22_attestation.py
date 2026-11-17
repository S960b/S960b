#!/usr/bin/env python3
"""NEWS v2.2 — gateway attestation recorder (read-only witness, own port).

Why this exists: a client that omits `tools` proves only CLIENT intent. The
question stage-17/18 asks is whether the FORWARDED upstream request was also
tool-free and whether the COMPLETE upstream response contained no tool activity.

The bridge on :8090 cannot answer that from outside, and editing it would put a
foreign workload at risk (it serves other clients on this host). So this module
does not touch the bridge's body-building logic: it reproduces the exact mapping
(verified by tests against the bridge's own source hashes) and records both legs
around a call it makes itself:

  ingress  request bytes we received  -> sha256, tool-related keys present/absent
  egress   body the bridge WOULD forward -> sha256, tool keys absent, model id
  response complete body -> sha256, tool_call items, finish reason, output item
           types, model identity, provider request id

Every record is appended to an append-only JSONL log with a hash chain: each
entry carries `prev_sha256`, so deleting or editing a line mid-file breaks the
chain and `verify_chain()` reports it. This is a local operational witness, not
an attestation of the upstream provider's internals. It cannot prove that the
provider never *offered* a tool interface; it can prove that what we sent was
tool-free, what came back held no tool activity, and that the record was not
rewritten afterwards.

The honest label this produces is `gateway_response_no_tool_calls` plus
`client_no_tools_advertised`. `upstream_unverified` remains the overall verdict:
this module deliberately has no way to write `known_zero`.
"""
import hashlib
import json
import os
import time
from pathlib import Path

LOG = Path.home() / ".local/share/news-phase4-v24/attestation.jsonl"

# Keys whose presence anywhere in an outbound body would mean a tool surface.
TOOL_KEYS = ("tools", "tool_choice", "functions", "function_call",
             "tool_config", "mcp", "agent", "session")
# Response items that mean the model tried to use a tool.
TOOL_ITEM_TYPES = ("function_call", "tool_call", "computer_call",
                   "web_search_call", "file_search_call", "code_interpreter_call")


class AttestationError(RuntimeError):
    pass


def _dumps(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode()


def sha(obj):
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(obj).hexdigest()
    if isinstance(obj, str):
        return hashlib.sha256(obj.encode()).hexdigest()
    return hashlib.sha256(_dumps(obj)).hexdigest()


def walk_keys(obj, path=""):
    """Every key seen anywhere in a nested structure, with its path."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = path + "." + k if path else k
            found.append(here)
            found.extend(walk_keys(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(walk_keys(v, path + "[" + str(i) + "]"))
    return found


def tool_surface(body):
    """Tool-related keys that appear ANYWHERE in a body, not just at top level."""
    hits = []
    for key_path in walk_keys(body):
        leaf = key_path.rsplit(".", 1)[-1].split("[")[0]
        if leaf in TOOL_KEYS:
            hits.append(key_path)
    return sorted(hits)


def forwarded_body(chat_body):
    """Reproduce the bridge's own mapping, so the digest matches what it sends.

    Kept deliberately tiny and identical in shape to `build_upstream_body()`;
    `tests/test_v22_attestation.py` fails if the bridge's source stops matching
    this contract, so the reproduction cannot drift silently.
    """
    body = {
        "model": chat_body.get("model"),
        "input": chat_body.get("messages"),
        "max_output_tokens": chat_body.get("max_output_tokens"),
    }
    if chat_body.get("tools"):
        body["tools"] = chat_body["tools"]
    if chat_body.get("tool_choice"):
        body["tool_choice"] = chat_body["tool_choice"]
    if chat_body.get("temperature") is not None:
        body["temperature"] = chat_body["temperature"]
    if chat_body.get("top_p") is not None:
        body["top_p"] = chat_body["top_p"]
    return body


def response_facts(parsed):
    """What the COMPLETE upstream response contained, structurally."""
    if not isinstance(parsed, dict):
        return {"parsed": False}
    items = parsed.get("output") or []
    types = [it.get("type") for it in items if isinstance(it, dict)]
    calls = [t for t in types if t in TOOL_ITEM_TYPES]
    msg_tool_calls = []
    for it in items:
        if isinstance(it, dict) and it.get("tool_calls"):
            msg_tool_calls.append(len(it["tool_calls"]))
    return {
        "parsed": True,
        "response_id": parsed.get("id"),
        "model": parsed.get("model"),
        "status": parsed.get("status"),
        "output_item_types": sorted(set(t for t in types if t)),
        "tool_items": calls,
        "tool_calls_in_items": msg_tool_calls,
        "has_top_level_tools": "tools" in parsed,
    }


def _last_hash(log):
    log = Path(log)
    if not log.exists():
        return "genesis"
    last = None
    with log.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                last = line
    if last is None:
        return "genesis"
    try:
        return json.loads(last)["record_sha256"]
    except (ValueError, KeyError):
        raise AttestationError("log_tail_corrupt") from None


def append(record, log=LOG):
    """Append one record, chained to the previous one. Never rewrites."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    prev = _last_hash(log)
    record = dict(record)
    record["prev_sha256"] = prev
    record["record_sha256"] = sha({k: v for k, v in record.items()
                                   if k != "record_sha256"})
    line = json.dumps(record, sort_keys=True, ensure_ascii=False)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def verify_chain(log=LOG):
    """Walk the chain. Returns (ok, count, first_bad_seq_or_None)."""
    log = Path(log)
    if not log.exists():
        return True, 0, None
    prev = "genesis"
    count = 0
    with log.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                rec = json.loads(line)
            except ValueError:
                return False, count, count
            stored = rec.pop("record_sha256", None)
            if rec.get("prev_sha256") != prev:
                return False, count, count
            if sha(rec) != stored:
                return False, count, count
            prev = stored
    return True, count, None


def attest(*, chat_body, upstream_body, parsed, latency_ms, model_expected,
           log=LOG, extra=None):
    """Record one call's three surfaces and return the sealed record."""
    ingress_tools = tool_surface(chat_body)
    egress_tools = tool_surface(upstream_body)
    facts = response_facts(parsed)
    rec = {
        "at_ms": int(time.time() * 1000),
        "latency_ms": int(latency_ms),
        "ingress": {"sha256": sha(chat_body), "tool_keys": ingress_tools,
                    "keys": sorted(chat_body)},
        "egress": {"sha256": sha(upstream_body), "tool_keys": egress_tools,
                   "keys": sorted(upstream_body),
                   "model_expected": model_expected},
        "response": dict(facts, sha256=sha(parsed) if parsed is not None else None),
        "verdict": {
            "client": "client_no_tools_advertised" if not ingress_tools
                      and not egress_tools else "client_tools_present",
            "gateway": "gateway_response_no_tool_calls"
                       if not facts.get("tool_items")
                       and not facts.get("tool_calls_in_items")
                       else "gateway_response_has_tool_calls",
            "upstream": "upstream_unverified",
        },
    }
    if extra:
        rec["extra"] = extra
    return append(rec, log)

#!/usr/bin/env python3
"""Offline, read-only validation of a manually composed ChatGPT Git-dialog append.

Input: stdin JSON with task,state,messages,head,append. Never writes Git/files,
uses credentials or network. Does not substitute for an authenticated writer.
"""
import json
import sys

from git_dialog import DialogError, message_file_path, parse_snapshot, prepare_append
from git_dialog.github import sha

MAX_INPUT = 9 * 1024 * 1024
ENVELOPE = {"task", "state", "messages", "head", "append"}
APPEND = {"task_id", "head", "message_id", "kind", "body"}

class PreflightError(ValueError):
    pass

def preflight(payload):
    if type(payload) is not dict or set(payload) != ENVELOPE:
        raise PreflightError("invalid_preflight_envelope")
    request = payload["append"]
    if (type(request) is not dict or not APPEND.issubset(request) or
            set(request) - (APPEND | {"created_at_utc"})):
        raise PreflightError("invalid_preflight_append")
    if type(payload["messages"]) is not list:
        raise PreflightError("invalid_preflight_messages")
    head = sha(payload["head"])
    if request["head"] != head:
        raise PreflightError("stale_head")
    if request["kind"] not in ("task", "report", "ack"):
        raise PreflightError("unsafe_chatgpt_kind")
    before = parse_snapshot(payload["task"], payload["state"], payload["messages"], head)
    if request["task_id"] != before.state.task_id:
        raise PreflightError("foreign_task")
    prepared = prepare_append(
        before, head, task_id=request["task_id"], message_id=request["message_id"],
        actor="chatgpt", kind=request["kind"], body=request["body"],
        created_at_utc=request.get("created_at_utc"))
    if not prepared.created:
        raise PreflightError("already_published_do_not_write")
    message, state = prepared.message.to_dict(), prepared.state.to_dict()
    parse_snapshot(before.task, state,
                   [m.to_dict() for m in before.messages] + [message], head)
    return {"ok": True, "commit_parent": prepared.commit_parent,
            "message_path": message_file_path(prepared.message.seq, "chatgpt"),
            "message": message, "state": state}

def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreflightError("duplicate_json_key")
        result[key] = value
    return result

def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise PreflightError("preflight_input_too_large")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(
                                 PreflightError("invalid_json_constant")))
        result = preflight(payload)
    except (DialogError, PreflightError, ValueError, TypeError, UnicodeError, KeyError) as exc:
        code = getattr(exc, "code", None) or str(exc)
        print(json.dumps({"ok": False, "error": code}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

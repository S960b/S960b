"""P36 direct Git writer/reader for the local MT agent.

This CLI NEVER uses the Hermes MCP/Unix authority to transfer message bodies.
Each invocation has an explicit local body path. It uses the verified
GitHubDialog implementation (atomic message + state tree, fast-forward ref).
No retry after an uncertain write; reconcile by message ID via GET only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from . import DialogError, validate_message_id, validate_task_id
from .github import GitHubDialog, JsonHttp, sha
from .lifecycle import LifecycleRegistry
from .preflight import read_configured_token

DEFAULT_TOKEN = "/home/kali/.config/agent-dialogs/token"
DEFAULT_REGISTRY = "/home/kali/agent-dialog-beta/state/lifecycle.p21.sqlite"
DEFAULT_CREATIONS = "/home/kali/agent-dialog-beta/state/authority-creations.p30.sqlite"
DEFAULT_API_CACHE = "/home/kali/agent-dialog-beta/state/github-api-cache.p361.sqlite"
MAX_BODY = 64 * 1024


def read_body(path: str) -> str:
    """A caller-named local UTF-8 file, not a Git URL or message-supplied path."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        import stat
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_nlink != 1 or st.st_size > MAX_BODY):
            raise DialogError("direct_body_file_rejected")
        raw = os.read(fd, MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise DialogError("body_too_large")
        return raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        raise DialogError("direct_body_file_rejected") from None
    finally:
        if fd is not None:
            os.close(fd)


def read_dialog_titles(task_ids, creations=DEFAULT_CREATIONS):
    """Read only local registered titles, no Git/body or Unix authority IPC."""
    if not task_ids:
        return {}
    path = Path(creations)
    try:
        st = path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            return {}
        # This is a fixed local application-owned DB, never request-selected.
        uri = "file:" + str(path) + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            result = {}
            for task_id in task_ids:
                validate_task_id(task_id)
                row = conn.execute(
                    "SELECT title FROM creations WHERE task_id=? AND confirmed=1",
                    (task_id,)).fetchone()
                if row is not None and isinstance(row[0], str):
                    result[task_id] = row[0]
            return result
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError):
        return {}


def describe(snapshot, limit: int = 20) -> dict:
    """Metadata only. Caller reads any actual body from Git, not MCP."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DialogError("invalid_dialog_page")
    return {
        "task_id": snapshot.state.task_id,
        "head": snapshot.head,
        "seq": snapshot.state.seq,
        "waiting_for": snapshot.state.next_actor,
        "status": snapshot.state.status,
        "messages": [
            {"seq": m.seq, "message_id": m.message_id,
             "from": m.sender, "to": m.recipient, "kind": m.kind}
            for m in snapshot.messages[-limit:]
        ],
    }


def append_mt(dialog, registry, task_id: str, expected_head: str,
              message_id: str, kind: str, body: str) -> dict:
    """Local MT-owned publication, no bridge payload and no implicit retry.

    The registry gate serializes local MT writes with local cancel/delete.
    Remote ChatGPT writes are protected by Git fast-forward ref update.
    """
    validate_task_id(task_id)
    validate_message_id(message_id)
    sha(expected_head)
    if kind not in ("report", "task", "ack", "close"):
        raise DialogError("direct_kind_rejected")
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY:
        raise DialogError("body_too_large")
    with registry.writing(task_id):
        before = dialog.load(task_id)
        if before.head != expected_head:
            raise DialogError("stale_head")
        receipt = dialog.append(task_id, expected_head,
                                message_id=message_id, actor="mt",
                                kind=kind, body=body)
        return {"message_id": receipt.message.message_id,
                "seq": receipt.message.seq,
                "head": receipt.commit_sha,
                "created": receipt.created}


def _reconcile_mt(dialog, args, body):
    """GET-only reconciliation, never retries a Git mutation."""
    try:
        snapshot = dialog.load(args.task_id)
        found = next((m for m in snapshot.messages
                      if m.message_id == args.message_id), None)
        if (found is not None and found.sender == "mt" and
                found.kind == args.kind and found.body == body):
            return {"message_id": found.message_id, "seq": found.seq,
                    "head": snapshot.head, "created": False, "reconciled": True}
    except Exception:
        pass
    return {"ok": False, "code": "publication_unconfirmed",
            "retry_authorized": False}


def notify_mt_commit(receipt):
    """Optional post-commit signal. Never retransmits content or Git writes."""
    if (type(receipt) is not dict or
            not all(k in receipt for k in ("message_id", "head", "seq", "task_id"))):
        return False
    try:
        from .unix_authority import SOCKETS, UnixClient
        reply = UnixClient(SOCKETS["mt"]).call({
            "op": "notice", "task_id": receipt["task_id"],
            "message_id": receipt["message_id"],
            "head": receipt["head"], "seq": receipt["seq"]})
        return reply.get("observed") is True
    except Exception:
        # Lost signal NEVER triggers a Git re-publish.
        # Gateway's Git-ref scanner reconciles the committed message.
        return False


def run(argv=None):
    parser = argparse.ArgumentParser(description="P36 direct local Git, no Hermes body")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    sub = parser.add_subparsers(dest="operation", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=50)
    show = sub.add_parser("peek")
    show.add_argument("--task-id", required=True)
    show.add_argument("--limit", type=int, default=20)
    read = sub.add_parser("read")
    read.add_argument("--task-id", required=True)
    read.add_argument("--seq", type=int, required=True)
    read.add_argument("--message-id", required=True)
    write = sub.add_parser("append")
    write.add_argument("--task-id", required=True)
    write.add_argument("--head", required=True)
    write.add_argument("--message-id", required=True)
    write.add_argument("--kind", choices=["report", "task", "ack", "close"], required=True)
    write.add_argument("--body-file", required=True)
    args = parser.parse_args(argv)

    registry = None
    try:
        registry = LifecycleRegistry(args.registry, existing=True)
        if args.operation == "list":
            rows, next_offset = registry.page(offset=args.offset, limit=args.limit)
            titles = read_dialog_titles([row["task_id"] for row in rows])
            result = {"dialogs": [
                {"task_id": row["task_id"], "lifecycle": row["state"],
                 "title": titles.get(row["task_id"])}
                for row in rows], "next_offset": next_offset}
        else:
            row = registry.get(args.task_id)
            if row["state"] != "active":
                raise DialogError("dialog_not_writable")
            # Token is read only in this local process; never in Hermes payload.
            dialog = GitHubDialog(JsonHttp(read_configured_token(args.token_file), timeout=15,
                                        budget_path=Path(args.registry).parent / Path(DEFAULT_API_CACHE).name))
            if args.operation == "peek":
                result = describe(dialog.load(args.task_id), args.limit)
            elif args.operation == "read":
                validate_message_id(args.message_id)
                snap = dialog.load(args.task_id)
                if type(args.seq) is not int or not 1 <= args.seq <= snap.state.seq:
                    raise DialogError("direct_message_not_found")
                msg = snap.messages[args.seq - 1]
                if msg.message_id != args.message_id:
                    raise DialogError("direct_message_not_found")
                result = {"task_id": args.task_id, "head": snap.head,
                          "seq": msg.seq, "message_id": msg.message_id,
                          "from": msg.sender, "to": msg.recipient, "kind": msg.kind,
                          "body": msg.body}
            else:
                body = read_body(args.body_file)
                try:
                    result = append_mt(dialog, registry, args.task_id,
                                       args.head, args.message_id, args.kind, body)
                except DialogError as exc:
                    if exc.code in ("stale_head", "wrong_turn", "closed_task",
                                    "message_id_conflict", "invalid_body",
                                    "body_too_large", "dialog_not_writable"):
                        raise
                    result = _reconcile_mt(dialog, args, body)
                except Exception:
                    result = _reconcile_mt(dialog, args, body)
        if args.operation == "append" and result.get("ok") is not False:
            # Already committed; this emits ONLY task/message IDs and SHA.
            result["notified"] = notify_mt_commit({
                "task_id": args.task_id, "message_id": result["message_id"],
                "head": result["head"], "seq": result["seq"]})
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ok") is not False else 2
    except DialogError as exc:
        print(json.dumps({"ok": False, "code": exc.code,
                          "retry_authorized": False}))
        return 2
    except Exception:
        print('{"ok":false,"code":"direct_git_unavailable","retry_authorized":false}')
        return 2
    finally:
        if registry is not None:
            registry.close()


if __name__ == "__main__":
    sys.exit(run())

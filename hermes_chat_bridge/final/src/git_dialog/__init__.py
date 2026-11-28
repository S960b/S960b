"""git-dialog.v1 — эталонная модель протокола передачи хода через Git.

Только чистые правила и fake-ref-store. Сетевых вызовов здесь нет.

Исправления по ревью:
- повтор ранее отправленного сообщения (delayed redelivery) — no-op, не conflict;
- полная валидация snapshot/task/state/messages;
- строгая валидация message_id и created_at_utc;
- task_id проверяется fullmatch (запрет управляющих символов и перевода строки);
- FakeRefStore потокобезопасен (Lock), конкурентная запись проверяется тестом.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

PROTOCOL = "git-dialog"
VERSION = "1"
ACTORS = ("chatgpt", "mt")
STATUSES = ("waiting", "working", "error", "done", "closing")
KINDS = ("task", "report", "ack", "close", "return")
MAX_BODY_BYTES = 64 * 1024

TASK_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,63}")
MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

BLOCKING_STATUSES = ("done", "closing")

RETRYABLE_OUTCOMES = ("not_started",)


class DialogError(Exception):
    """Ошибка протокола с машинным кодом."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def auto_retry_allowed(outcome: str) -> bool:
    """Автоповтор допустим только если внешний эффект точно не начался.

    'unknown' (сбой после эффекта до durable result) повтор НЕ разрешает.
    """
    return outcome in RETRYABLE_OUTCOMES


def _has_control(s: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in s)


def validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise DialogError("invalid_task_id", repr(task_id))
    if _has_control(task_id):
        raise DialogError("invalid_task_id", "control characters")
    return task_id


def validate_message_id(message_id: str) -> str:
    if not isinstance(message_id, str) or not MESSAGE_ID_RE.fullmatch(message_id):
        raise DialogError("invalid_message_id", repr(message_id))
    if _has_control(message_id):
        raise DialogError("invalid_message_id", "control characters")
    return message_id


def validate_timestamp(ts: str) -> str:
    if not isinstance(ts, str) or not TIMESTAMP_RE.fullmatch(ts):
        raise DialogError("invalid_timestamp", repr(ts))
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")  # календарная корректность
    except ValueError:
        raise DialogError("invalid_timestamp", repr(ts)) from None
    return ts


def validate_rel_path(path: str) -> str:
    """Разрешает только относительные безопасные пути без .. и абсолютов."""
    if not isinstance(path, str) or not path:
        raise DialogError("invalid_path", repr(path))
    if path.startswith("/") or "\\" in path:
        raise DialogError("invalid_path", path)
    if _has_control(path):
        raise DialogError("invalid_path", "control characters")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise DialogError("unsafe_path", path)
    return path


def validate_int(value, name: str, minimum: int = 0) -> int:
    """Строгий int: bool отвергается (bool — подкласс int)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise DialogError(f"invalid_{name}", repr(value))
    if value < minimum:
        raise DialogError(f"invalid_{name}", repr(value))
    return value


def branch_ref(task_id: str) -> str:
    """Git-ref беседы. Файлы лежат ВНУТРИ этой ветки."""
    return f"dialogs/{validate_task_id(task_id)}"


def message_path(task_id: str, seq: int, actor: str) -> str:
    """Полный путь файла в репозитории: <ref>/messages/<seq>.<actor>.json."""
    ref = branch_ref(task_id)
    if actor not in ACTORS:
        raise DialogError("unknown_actor", actor)
    validate_int(seq, "seq", 1)
    return validate_rel_path(f"{ref}/messages/{seq}.{actor}.json")


def message_file_path(seq: int, actor: str) -> str:
    """Actual path inside a dialogue branch; do not prefix it with the ref."""
    validate_int(seq, "seq", 1)
    if actor not in ACTORS:
        raise DialogError("unknown_actor", str(actor))
    return f"messages/{seq}.{actor}.json"


def other_actor(actor: str) -> str:
    if actor not in ACTORS:
        raise DialogError("unknown_actor", actor)
    return "mt" if actor == "chatgpt" else "chatgpt"


@dataclass(frozen=True)
class Message:
    task_id: str
    message_id: str
    seq: int
    sender: str
    recipient: str
    reply_to: str | None
    kind: str
    created_at_utc: str
    body: str

    def to_dict(self) -> dict:
        return {
            "protocol": PROTOCOL, "version": VERSION, "task_id": self.task_id,
            "message_id": self.message_id, "seq": self.seq, "from": self.sender,
            "to": self.recipient, "reply_to": self.reply_to, "kind": self.kind,
            "created_at_utc": self.created_at_utc, "body": self.body,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class TaskState:
    task_id: str
    revision: int
    seq: int
    last_message_id: str | None
    next_actor: str
    status: str

    def to_dict(self) -> dict:
        return {
            "protocol": PROTOCOL, "version": VERSION, "task_id": self.task_id,
            "revision": self.revision, "last_message_id": self.last_message_id,
            "seq": self.seq, "next_actor": self.next_actor, "status": self.status,
        }


@dataclass
class Snapshot:
    """Прочитанное состояние ветки: task, state, сообщения и head."""

    task: dict
    state: TaskState
    messages: list[Message] = field(default_factory=list)
    head: str = ""

    @property
    def last(self) -> Message | None:
        return self.messages[-1] if self.messages else None


@dataclass
class Prepared:
    message: Message
    state: TaskState
    commit_parent: str
    created: bool = True


def _check_body(body: str) -> str:
    if not isinstance(body, str):
        raise DialogError("invalid_body", type(body).__name__)
    try:
        size = len(body.encode("utf-8"))
    except UnicodeError:
        raise DialogError("invalid_body", "not valid UTF-8") from None
    if size > MAX_BODY_BYTES:
        raise DialogError("body_too_large", f">{MAX_BODY_BYTES} bytes")
    return body


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_task(task: dict) -> None:
    if not isinstance(task, dict):
        raise DialogError("invalid_task", type(task).__name__)
    if task.get("protocol") != PROTOCOL:
        raise DialogError("unknown_protocol", str(task.get("protocol")))
    if task.get("version") != VERSION:
        raise DialogError("unknown_version", str(task.get("version")))
    validate_task_id(task.get("task_id"))
    if not isinstance(task.get("title"), str) or not task["title"].strip():
        raise DialogError("invalid_task", "missing or empty title")
    if task.get("initiator", "chatgpt") not in ACTORS:
        raise DialogError("invalid_task", "invalid initiator")


def validate_state(state: TaskState) -> None:
    if not isinstance(state, TaskState):
        raise DialogError("invalid_state", type(state).__name__)
    validate_task_id(state.task_id)
    if state.status not in STATUSES:
        raise DialogError("invalid_status", str(state.status))
    if state.next_actor not in ACTORS:
        raise DialogError("unknown_actor", str(state.next_actor))
    validate_int(state.revision, "revision", 0)
    validate_int(state.seq, "seq", 0)
    if state.seq == 0 and state.last_message_id is not None:
        raise DialogError("invalid_state", "last_message_id without messages")
    if state.seq > 0 and state.last_message_id is None:
        raise DialogError("invalid_state", "missing last_message_id")
    if state.last_message_id is not None:
        validate_message_id(state.last_message_id)
    if state.revision < state.seq:
        raise DialogError("invalid_state", "revision behind message sequence")


def validate_snapshot(snapshot: Snapshot) -> None:
    """Полная проверка прочитанных данных перед подготовкой записи."""
    if not isinstance(snapshot, Snapshot):
        raise DialogError("invalid_snapshot", type(snapshot).__name__)
    validate_task(snapshot.task)
    validate_state(snapshot.state)
    if snapshot.task.get("task_id") != snapshot.state.task_id:
        raise DialogError("foreign_task", "task/state mismatch")
    if not isinstance(snapshot.messages, list):
        raise DialogError("invalid_snapshot", "messages must be a list")
    if not isinstance(snapshot.head, str) or not snapshot.head or _has_control(snapshot.head):
        raise DialogError("invalid_snapshot", "invalid head")

    prev = None
    # Legacy dialogues started with ChatGPT. New, independent dialogues may be
    # opened by either authenticated peer, pinned in immutable task.json.
    expected_actor = snapshot.task.get("initiator", "chatgpt")
    for i, m in enumerate(snapshot.messages, start=1):
        if not isinstance(m, Message):
            raise DialogError("invalid_message", type(m).__name__)
        validate_task_id(m.task_id)
        validate_message_id(m.message_id)
        validate_timestamp(m.created_at_utc)
        _check_body(m.body)
        if m.task_id != snapshot.state.task_id:
            raise DialogError("foreign_task", m.message_id)
        validate_int(m.seq, "seq", 1)
        if m.seq != i:
            raise DialogError("invalid_seq", f"{m.seq} != {i}")
        if m.sender not in ACTORS or m.recipient not in ACTORS:
            raise DialogError("unknown_actor", m.message_id)
        if m.sender == m.recipient:
            raise DialogError("invalid_message", "sender == recipient")
        if m.kind not in KINDS:
            raise DialogError("unknown_kind", m.message_id)
        if m.reply_to != prev:
            raise DialogError("bad_reply_to", m.message_id)
        if m.kind == "return":
            # This is an OWNER-authenticated control event, not a peer reply.
            # The recorded sender is the actor whose turn was returned, solely
            # to preserve the two-actor wire format. UI must label it "owner".
            previous = snapshot.messages[i - 2] if i >= 2 else None
            if (previous is None or previous.kind in ("return", "close") or
                    m.message_id != f"owner_return_{i}" or
                    m.body != "OWNER_RETURN_LAST:" + previous.message_id):
                raise DialogError("invalid_owner_return")
        if m.sender != expected_actor:
            raise DialogError("bad_history_turn", m.message_id)
        if m.kind == "close" and i != len(snapshot.messages):
            raise DialogError("closed_task", "messages after close")
        expected_actor = m.recipient
        prev = m.message_id

    if snapshot.state.seq != len(snapshot.messages):
        raise DialogError("invalid_state", "seq != messages count")
    if snapshot.messages and snapshot.state.last_message_id != prev:
        raise DialogError("invalid_state", "last_message_id != tail")
    ids = [m.message_id for m in snapshot.messages]
    if len(ids) != len(set(ids)):
        raise DialogError("invalid_state", "duplicate message_id")
    if snapshot.state.next_actor != expected_actor:
        raise DialogError("invalid_state", "next_actor inconsistent with history")
    if snapshot.messages and snapshot.last.kind == "close" and snapshot.state.status not in BLOCKING_STATUSES:
        raise DialogError("invalid_state", "close message without terminal state")


def parse_task(obj) -> dict:
    """Разбор/валидация task.json. Повреждённые данные отвергаются."""
    if not isinstance(obj, dict):
        raise DialogError("invalid_task", type(obj).__name__)
    for k in ("protocol", "version", "task_id", "title"):
        if k not in obj:
            raise DialogError("invalid_task", f"missing {k}")
    validate_task(obj)
    if not isinstance(obj["title"], str) or not obj["title"].strip():
        raise DialogError("invalid_task", "empty title")
    return obj


def parse_state(obj) -> TaskState:
    """Разбор/валидация state.json."""
    if not isinstance(obj, dict):
        raise DialogError("invalid_state", type(obj).__name__)
    for k in ("protocol", "version", "task_id", "revision", "seq",
              "last_message_id", "next_actor", "status"):
        if k not in obj:
            raise DialogError("invalid_state", f"missing {k}")
    if obj["protocol"] != PROTOCOL:
        raise DialogError("unknown_protocol", str(obj["protocol"]))
    if obj["version"] != VERSION:
        raise DialogError("unknown_version", str(obj["version"]))
    last = obj["last_message_id"]
    if last is not None:
        validate_message_id(last)
    st = TaskState(task_id=obj["task_id"], revision=obj["revision"], seq=obj["seq"],
                   last_message_id=last, next_actor=obj["next_actor"],
                   status=obj["status"])
    validate_state(st)
    return st


def parse_message(obj) -> Message:
    """Разбор/валидация одного файла сообщения."""
    if not isinstance(obj, dict):
        raise DialogError("invalid_message", type(obj).__name__)
    for k in ("protocol", "version", "task_id", "message_id", "seq", "from",
              "to", "reply_to", "kind", "created_at_utc", "body"):
        if k not in obj:
            raise DialogError("invalid_message", f"missing {k}")
    if obj["protocol"] != PROTOCOL:
        raise DialogError("unknown_protocol", str(obj["protocol"]))
    if obj["version"] != VERSION:
        raise DialogError("unknown_version", str(obj["version"]))
    validate_task_id(obj["task_id"])
    validate_message_id(obj["message_id"])
    validate_int(obj["seq"], "seq", 1)
    if obj["from"] not in ACTORS or obj["to"] not in ACTORS or obj["from"] == obj["to"]:
        raise DialogError("unknown_actor", str(obj["from"]))
    if obj["kind"] not in KINDS:
        raise DialogError("unknown_kind", str(obj["kind"]))
    if obj["reply_to"] is not None:
        validate_message_id(obj["reply_to"])
    validate_timestamp(obj["created_at_utc"])
    _check_body(obj["body"])
    return Message(task_id=obj["task_id"], message_id=obj["message_id"],
                   seq=obj["seq"], sender=obj["from"], recipient=obj["to"],
                   reply_to=obj["reply_to"], kind=obj["kind"],
                   created_at_utc=obj["created_at_utc"], body=obj["body"])


def parse_snapshot(task_obj, state_obj, message_objs, head: str) -> Snapshot:
    """Собрать и полностью проверить snapshot из сырых JSON-структур."""
    if not isinstance(message_objs, list):
        raise DialogError("invalid_snapshot", "messages must be a list")
    snap = Snapshot(task=parse_task(task_obj), state=parse_state(state_obj),
                    messages=[parse_message(m) for m in message_objs],
                    head=head)
    validate_snapshot(snap)
    return snap


def prepare_append(
    snapshot: Snapshot,
    expected_head: str,
    *,
    task_id: str,
    message_id: str,
    actor: str,
    kind: str,
    body: str,
    created_at_utc: str | None = None,
) -> Prepared:
    """Чисто подготовить новое сообщение и новое состояние.

    Исходный snapshot не мутируется. Возвращает одну предполагаемую транзакцию.
    """
    validate_snapshot(snapshot)
    validate_task_id(task_id)
    validate_message_id(message_id)
    if created_at_utc is not None:
        validate_timestamp(created_at_utc)
    if task_id != snapshot.state.task_id:
        raise DialogError("foreign_task", f"{task_id} != {snapshot.state.task_id}")
    if actor not in ACTORS:
        raise DialogError("unknown_actor", actor)
    if kind not in KINDS:
        raise DialogError("unknown_kind", kind)
    _check_body(body)
    if expected_head != snapshot.head:
        raise DialogError("stale_head", f"{expected_head} != {snapshot.head}")

    recipient = other_actor(actor)

    # Идемпотентность: повтор любого ранее принятого сообщения — no-op,
    # в том числе если это уже не последний ход (delayed redelivery).
    existing = next((m for m in snapshot.messages if m.message_id == message_id), None)
    if existing is not None:
        same = (
            existing.task_id == task_id
            and existing.sender == actor
            and existing.recipient == recipient
            and existing.kind == kind
            and existing.body == body
            and (created_at_utc is None or existing.created_at_utc == created_at_utc)
        )
        if same:
            return Prepared(message=existing, state=snapshot.state,
                            commit_parent=snapshot.head, created=False)
        raise DialogError("message_id_conflict", message_id)

    if snapshot.state.status in BLOCKING_STATUSES:
        raise DialogError("closed_task", snapshot.state.status)
    if snapshot.state.next_actor != actor:
        raise DialogError("wrong_turn", f"expected {snapshot.state.next_actor}")
    if kind == "return":
        previous = snapshot.last
        if (previous is None or previous.kind in ("return", "close") or
                previous.sender == actor or
                message_id != f"owner_return_{snapshot.state.seq + 1}" or
                body != "OWNER_RETURN_LAST:" + previous.message_id):
            raise DialogError("invalid_owner_return")

    seq = snapshot.state.seq + 1
    msg = Message(
        task_id=task_id, message_id=message_id, seq=seq, sender=actor,
        recipient=recipient, reply_to=snapshot.state.last_message_id, kind=kind,
        created_at_utc=created_at_utc or _utc_now(), body=body,
    )
    status = snapshot.state.status
    if kind == "close":
        status = "done"
    elif status == "waiting":
        status = "working"
    new_state = TaskState(
        task_id=task_id, revision=snapshot.state.revision + 1, seq=seq,
        last_message_id=message_id, next_actor=recipient, status=status,
    )
    return Prepared(message=msg, state=new_state, commit_parent=snapshot.head, created=True)


class FakeRefStore:
    """Фейковый ref-store с контрактом CAS и блокировкой. Не GitHub API."""

    def __init__(self) -> None:
        self._refs: dict[str, str] = {}
        self._lock = threading.Lock()

    def open_branch(self, ref: str, head: str) -> None:
        """Явное открытие беседы. Единственный способ создать ветку."""
        validate_rel_path(ref)
        with self._lock:
            if ref in self._refs:
                raise DialogError("branch_exists", ref)
            self._refs[ref] = head

    def head(self, ref: str) -> str | None:
        with self._lock:
            return self._refs.get(ref)

    def publish_cas(self, ref: str, expected_head: str, new_head: str) -> dict:
        """Атомарный fast-forward существующего ref. Иначе conflict/branch_missing."""
        with self._lock:
            current = self._refs.get(ref)
            if current is None:
                return {"status": "branch_missing", "ref": ref}
            if current != expected_head:
                return {"status": "conflict", "ref": ref, "head": current}
            self._refs[ref] = new_head
            return {"status": "ok", "ref": ref, "head": new_head}

    def delete_branch(self, ref: str) -> bool:
        with self._lock:
            return self._refs.pop(ref, None) is not None



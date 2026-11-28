"""One bounded step, opt-in PURE actions only. No polling loop or executor API.

Message bodies cannot select code, tools, paths, credentials or callbacks.
Malformed/unauthorized actions produce a durable refusal, never execution.
"""
import hashlib
import json
from datetime import datetime, timezone

from . import DialogError, Message, validate_message_id, validate_task_id
from .github import canonical, decode_json
from .gateway import ActorPort
from .lifecycle import LifecycleDialog
from .publication import publish_saved

OPERATIONS = frozenset(("ping", "sha256"))
REPOSITORY = "S960b/agent-dialogs"


def _policy(allowed_ops):
    if not isinstance(allowed_ops, (tuple, list, set, frozenset)):
        raise DialogError("invalid_worker_policy")
    if any(not isinstance(op, str) or op not in OPERATIONS for op in allowed_ops):
        raise DialogError("invalid_worker_policy")
    return frozenset(allowed_ops)


def validate_action(body, allowed_ops=()):
    """Validate inert schema/policy without performing or starting an action."""
    allowed = _policy(allowed_ops)
    if not isinstance(body, str):
        raise DialogError("invalid_action")
    try:
        if len(body.encode("utf-8")) > 4096:
            raise DialogError("action_too_large")
    except UnicodeError:
        raise DialogError("invalid_action") from None
    try:
        action = decode_json(body)
    except RecursionError:
        raise DialogError("invalid_action") from None
    if not isinstance(action, dict):
        raise DialogError("invalid_action")
    if (action.get("protocol") != "git-dialog-action" or
            type(action.get("version")) is not int or action["version"] != 1):
        raise DialogError("unsupported_action_protocol")
    op = action.get("op")
    if not isinstance(op, str) or op not in allowed:
        raise DialogError("action_not_allowed")
    expected = {"protocol", "version", "op"} | ({"text"} if op == "sha256" else set())
    if set(action) != expected:
        raise DialogError("invalid_action_fields")
    if op == "sha256":
        text = action["text"]
        if not isinstance(text, str):
            raise DialogError("invalid_action_text")
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            raise DialogError("invalid_action_text") from None
        if len(raw) > 2048:
            raise DialogError("action_text_too_large")
    return action


def evaluate(body, allowed_ops=()):
    """Deterministic bounded computation, default DENY. Not a plugin interface."""
    action = validate_action(body, allowed_ops)
    op = action['op']
    value = 'pong' if op == 'ping' else hashlib.sha256(action['text'].encode('utf-8')).hexdigest()
    return {"protocol": "git-dialog-result", "version": 1, "ok": True, "op": op, "value": value}


class WorkerStep:
    """Bound task + fixed repository + mandatory lifecycle + durable journal.

    Caller configures allowed pure actions and IDs. There is no external-action
    callback. started/unknown records never trigger an automatic second start.
    """
    def __init__(self, journal, dialog, registry, task_id, *, allowed_ops=(), require_shared=False):
        validate_task_id(task_id)
        if dialog.repository != REPOSITORY:
            raise DialogError("worker_repository_forbidden")
        self.journal = journal
        self.registry = registry
        if type(require_shared) is not bool or (require_shared and
                (type(dialog) is not ActorPort or not dialog.durable_publication)):
            raise DialogError('worker_shared_authority_required')
        if type(dialog) is ActorPort:
            if dialog.registry is not registry:
                raise DialogError("worker_gateway_registry_mismatch")
            if dialog.actor != journal.actor:
                raise DialogError("worker_gateway_actor_mismatch")
            # The bound port already holds the mandatory lifecycle append gate.
            # Do not nest BEGIN IMMEDIATE on the same SQLite connection.
            self.dialog = dialog
        else:
            self.dialog = LifecycleDialog(dialog, registry)
        self.task_id = task_id
        self.allowed_ops = _policy(allowed_ops)

    def step(self, request_id, report_id, run_id):
        for value in (request_id, report_id, run_id):
            validate_message_id(value)
        if request_id == report_id:
            raise DialogError("invalid_report_id")
        task_id = self.task_id
        # Close/cancel cannot overtake receive/start/pure compute/save locally.
        # Publication gets its own lifecycle gate through LifecycleDialog.
        with self.registry.writing(task_id):
            snapshot = self.dialog.load(task_id)
            incoming = next((m for m in snapshot.messages if m.message_id == request_id), None)
            if incoming is None:
                raise DialogError("worker_request_missing")
            if incoming.kind != "task" or incoming.recipient != self.journal.actor:
                raise DialogError("not_executable_task")
            try:
                row = self.journal.get(task_id, request_id)
            except DialogError as exc:
                if exc.code != "execution_not_found":
                    raise
                if (snapshot.last != incoming or snapshot.state.next_actor != self.journal.actor or
                        snapshot.state.status in ("closing", "done")):
                    raise DialogError("worker_request_not_current")
                if any(m.message_id == report_id for m in snapshot.messages):
                    raise DialogError("worker_report_id_used")
            # Re-validate immutable input even when resuming an existing record.
            row = self.journal.receive(incoming)
            if row["status"] == "received":
                if (snapshot.last != incoming or snapshot.state.next_actor != self.journal.actor or
                        snapshot.state.status in ("closing", "done")):
                    raise DialogError("worker_request_not_current")
                if not self.journal.start(task_id, request_id, run_id):
                    raise DialogError("worker_start_not_granted")
                try:
                    result = evaluate(incoming.body, self.allowed_ops)
                except DialogError as exc:
                    result = {"protocol": "git-dialog-result", "version": 1,
                              "ok": False, "error": exc.code}
                report = Message(task_id, report_id, incoming.seq + 1, self.journal.actor,
                                 incoming.sender, request_id, "report",
                                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 canonical(result))
                row = self.journal.save_result(task_id, request_id, run_id, report)
            elif row["status"] in ("started", "unknown"):
                raise DialogError("worker_execution_unresolved")
            if row["run_id"] != run_id:
                raise DialogError("not_execution_owner")
            if row["result_message_id"] != report_id:
                raise DialogError("worker_report_id_conflict")
        published = publish_saved(self.journal, self.dialog, task_id, request_id, run_id)
        return {"task_id": task_id, "request_id": request_id, "report_id": report_id,
                "status": published["status"], "confirmed_commit": published["commit_sha"],
                "result": json.loads(published["result"])["body"]}


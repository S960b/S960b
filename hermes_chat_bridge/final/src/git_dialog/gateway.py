"""In-process shared writer gate and bounded panel projection, NOT a server.

Actor ports are trusted configuration, not authentication tokens. An eventual
authenticated gateway must be the ONLY path used by all cooperating writers.
No ref creation/deletion, service installation, arbitrary execution or UI code.
"""
from datetime import datetime, timezone

from . import ACTORS, DialogError, validate_snapshot, validate_task_id
from .github import sha
from .lifecycle import LifecycleDialog


class WriteGateway:
    def __init__(self, dialog, registry, task_id, *, actors=(), append_store=None):
        validate_task_id(task_id)
        if dialog.repository != "S960b/agent-dialogs":
            raise DialogError("gateway_repository_forbidden")
        if (not isinstance(actors, (tuple, list, set, frozenset)) or
                any(not isinstance(a, str) or a not in ACTORS for a in actors)):
            raise DialogError("invalid_gateway_policy")
        registry.get(task_id)  # Never auto-register, create or resurrect a task.
        self._dialog = LifecycleDialog(dialog, registry)
        if append_store is not None:
            from .append_store import AppendStore
            if type(append_store) is not AppendStore:
                raise DialogError('gateway_append_store_denied')
        self.append_store = append_store
        self._actors = frozenset(actors)
        self.task_id = task_id
        self.repository = dialog.repository

    def _append(self, task_id, expected_head, **kwargs):
        if self.append_store is None:
            return self._dialog.append(task_id, expected_head, **kwargs)
        with self._dialog.registry.writing(task_id):
            return self.append_store.publish(self._dialog.dialog, task_id, expected_head, **kwargs)

    def port(self, actor):
        if not isinstance(actor, str) or actor not in self._actors:
            raise DialogError("gateway_actor_denied")
        return ActorPort(self, actor)

    def load(self, task_id):
        if task_id != self.task_id:
            raise DialogError("gateway_task_denied")
        return self._dialog.load(task_id)


class ActorPort:
    """Duck-typed GitHubDialog port usable by publication/WorkerStep.

    Caller cannot select a different sender, task or repo through message data.
    Possession of this Python object is not proof of a real user's identity.
    """
    def __init__(self, gateway, actor):
        self._gateway = gateway
        self._actor = actor
        self.repository = gateway.repository

    def load(self, task_id):
        return self._gateway.load(task_id)

    @property
    def registry(self):
        return self._gateway._dialog.registry

    @property
    def actor(self):
        return self._actor

    @property
    def durable_publication(self):
        return self._gateway.append_store is not None

    def reconcile(self, task_id, message):
        self._authorize(task_id, message.sender)
        return self._gateway._dialog.reconcile(task_id, message)

    def _authorize(self, task_id, actor):
        if task_id != self._gateway.task_id:
            raise DialogError("gateway_task_denied")
        if actor != self._actor:
            raise DialogError("gateway_actor_denied")

    def append(self, task_id, expected_head, *, message_id, actor, kind, body,
               created_at_utc=None):
        self._authorize(task_id, actor)
        sha(expected_head)
        # Closing/deletion must use a future separate authenticated lifecycle API.
        # A conversation body cannot invoke it indirectly using kind='close'.
        if kind not in ("task", "report", "ack"):
            raise DialogError("gateway_message_kind_denied")
        return self._gateway._append(task_id, expected_head, message_id=message_id,
            actor=self._actor, kind=kind, body=body, created_at_utc=created_at_utc)


def panel_view(snapshot, *, now=None, offset=0, limit=20):
    """Bounded structured data only; strings MUST be rendered as text by a UI.

    Red timeout means 'no handoff for 10 minutes', not proof of worker failure.
    No credential paths, DB details, delete nonce or inferred heartbeat exposed.
    """
    validate_snapshot(snapshot)
    if (type(offset) is not int or offset < 0 or type(limit) is not int or
            not 1 <= limit <= 100):
        raise DialogError("invalid_panel_page")
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise DialogError("invalid_panel_time")
    last = snapshot.last if snapshot.messages else None
    age = None
    if last is not None:
        stamp = datetime.strptime(last.created_at_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = max(0, int((now - stamp).total_seconds()))
    state = snapshot.state
    terminal = state.status in ("closing", "done")
    stalled = not terminal and age is not None and age >= 600
    colors = {actor: "green" if last and last.sender == actor else "gray" for actor in ACTORS}
    if terminal:
        colors = {actor: "gray" for actor in ACTORS}
    else:
        colors[state.next_actor] = "red" if state.status == "error" or stalled else "yellow"
    page = snapshot.messages[offset:offset + limit]
    return {"task_id": state.task_id, "title": snapshot.task["title"], "head": snapshot.head,
            "status": state.status, "direction": {"from": last.sender, "to": last.recipient} if last else None,
            "waiting_for": None if terminal else state.next_actor, "actor_colors": colors,
            "stalled": stalled, "seconds_since_message": age,
            "last_message_at": last.created_at_utc if last else None,
            "history": [m.to_dict() for m in page], "total_messages": len(snapshot.messages),
            "next_offset": offset + limit if offset + limit < len(snapshot.messages) else None,
            "delete_available": False, "delete_blocker": "authenticated_remote_lifecycle_not_integrated"}

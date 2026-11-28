"""Offline-ready terminal barrier outbox; no delete API or HTTP server.

Explicit trusted lifecycle call, never selected by a conversation body. Uses a
SEPARATE durable DB. Prepare permanently cancels local publication. A failed or
uncertain send is NEVER resent automatically. All writers must share authority;
external ref rewind/recreate and loss of registries are outside this contract.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat

from . import DialogError, prepare_append, parse_message, validate_task_id
from .github import blob_sha, canonical, sha
from .journal import _wire

APPLICATION_ID = 0x47444342
BODY = "GIT_DIALOG_TERMINAL_BARRIER_V1_NO_EXECUTION"


class ClosingOutbox:
    def __init__(self, path):
        path = Path(path)
        self.conn = None
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        except OSError:
            raise DialogError("unsafe_closing_database") from None
        else:
            os.close(fd)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600):
            raise DialogError("unsafe_closing_database")
        try:
            self.conn = sqlite3.connect(path, timeout=15)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA synchronous=FULL")
            with self._tx():
                app = self.conn.execute("PRAGMA application_id").fetchone()[0]
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if app not in (0, APPLICATION_ID) or (app == 0 and tables):
                    raise DialogError("wrong_closing_database")
                if app == APPLICATION_ID and tables != {"barriers"}:
                    raise DialogError("closing_schema_mismatch")
                self.conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                self.conn.execute("""CREATE TABLE IF NOT EXISTS barriers (
                    task_id TEXT PRIMARY KEY, initial_head TEXT NOT NULL, message TEXT NOT NULL,
                    attempted INTEGER NOT NULL DEFAULT 0 CHECK(attempted IN (0,1)),
                    confirmed_commit TEXT
                )""")
        except BaseException:
            if self.conn is not None:
                self.conn.close()
            raise

    @contextmanager
    def _tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    def get(self, task_id):
        validate_task_id(task_id)
        row = self.conn.execute("SELECT * FROM barriers WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise DialogError("closing_not_prepared")
        return dict(row)

    def save(self, task_id, initial_head, message):
        sha(initial_head)
        if message.task_id != task_id or message.kind != "close" or message.body != BODY:
            raise DialogError("invalid_closing_message")
        wire = _wire(message)
        with self._tx():
            self.conn.execute("INSERT OR IGNORE INTO barriers(task_id,initial_head,message) VALUES (?,?,?)",
                              (task_id, initial_head, wire))
            row = self.get(task_id)
            if row["initial_head"] != initial_head or row["message"] != wire:
                raise DialogError("closing_intent_conflict")
        return row

    def reserve(self, task_id):
        with self._tx():
            self.get(task_id)
            changed = self.conn.execute("UPDATE barriers SET attempted=1 WHERE task_id=? AND "
                                        "attempted=0 AND confirmed_commit IS NULL", (task_id,)).rowcount
        return changed == 1

    def confirm(self, task_id, commit):
        sha(commit)
        with self._tx():
            row = self.get(task_id)
            if row["confirmed_commit"] not in (None, commit):
                raise DialogError("closing_confirmation_conflict")
            self.conn.execute("UPDATE barriers SET confirmed_commit=? WHERE task_id=?", (commit, task_id))
        return self.get(task_id)


class ClosingCoordinator:
    """Explicit terminal intent, separate from actor message ports.

    NOT user authentication/confirmation, conditional deletion or resurrection.
    Public API has no send-again/reset/undo/delete. Local cancel is permanent.
    """
    def __init__(self, dialog, registry, outbox, task_id):
        validate_task_id(task_id)
        if dialog.repository != "S960b/agent-dialogs":
            raise DialogError("closing_repository_forbidden")
        registry.get(task_id)
        self.dialog, self.registry, self.outbox, self.task_id = dialog, registry, outbox, task_id

    def prepare(self, expected_head, *, message_id, actor, created_at_utc=None):
        sha(expected_head)
        with self.registry.writing(self.task_id):
            snapshot = self.dialog.load(self.task_id)
            if snapshot.head != expected_head:
                raise DialogError("stale_head")
            prepared = prepare_append(snapshot, expected_head, task_id=self.task_id,
                message_id=message_id, actor=actor, kind="close", body=BODY,
                created_at_utc=created_at_utc)
            if not prepared.created:
                raise DialogError("closing_message_id_used")
            row = self.outbox.save(self.task_id, expected_head, prepared.message)
        # If a process stops between DBs, publish() repeats ONLY local cancel.
        # A writer winning this gap changes HEAD, so the barrier cannot append.
        self.registry.cancel(self.task_id)
        return row

    def _observe(self, row):
        message = parse_message(json.loads(row["message"]))
        snapshot = self.dialog.load(self.task_id)
        found = next((m for m in snapshot.messages if m.message_id == message.message_id), None)
        if found is None:
            if row["confirmed_commit"] is not None:
                raise DialogError("closing_barrier_missing")
            return None
        initial, _, old_tree = self.dialog._snapshot(self.task_id, row["initial_head"])
        prepared = prepare_append(initial, initial.head, task_id=self.task_id,
            message_id=message.message_id, actor=message.sender, kind="close", body=BODY,
            created_at_utc=message.created_at_utc)
        actual, commit, tree = self.dialog._snapshot(self.task_id, snapshot.head)
        parents = commit.get("parents")
        from . import message_file_path
        path = message_file_path(message.seq, message.sender)
        old_files = {p: e for p, e in old_tree.items() if p != "state.json" and e["type"] != "tree"}
        new_files = {p: e for p, e in tree.items() if p not in ("state.json", path) and e["type"] != "tree"}
        expected = {path: message.to_json(), "state.json": canonical(prepared.state.to_dict())}
        if (found != message or prepared.message != message or actual.task != initial.task or
                actual.messages != initial.messages + [message] or actual.state != prepared.state or
                not isinstance(parents, list) or len(parents) != 1 or
                parents[0].get("sha") != initial.head or new_files != old_files or
                any(tree.get(p) != {"mode": "100644", "type": "blob", "sha": blob_sha(content)}
                    for p, content in expected.items())):
            raise DialogError("closing_barrier_mismatch")
        return self.outbox.confirm(self.task_id, actual.head)

    def observe(self):
        """Remote reads only. Never obtains a publication grant or calls append."""
        row = self.outbox.get(self.task_id)
        observed = self._observe(row)
        if observed is None:
            raise DialogError("closing_unresolved" if row["attempted"] else "closing_not_sent")
        return observed

    def publish(self):
        row = self.outbox.get(self.task_id)
        local = self.registry.get(self.task_id)
        if local["state"] not in ("active", "cancelled"):
            raise DialogError("closing_local_state_forbidden")
        if local["state"] == "active":
            self.registry.cancel(self.task_id)
        observed = self._observe(row)
        if observed is not None:
            return observed
        if row["attempted"]:
            raise DialogError("closing_unresolved")
        message = parse_message(json.loads(row["message"]))
        if not self.outbox.reserve(self.task_id):
            raise DialogError("closing_unresolved")
        # Durable grant is committed BEFORE network mutation. No retry handler.
        self.dialog.append(self.task_id, row["initial_head"], message_id=message.message_id,
            actor=message.sender, kind="close", body=message.body, created_at_utc=message.created_at_utc)
        return self.observe()

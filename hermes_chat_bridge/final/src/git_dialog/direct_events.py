"""P36 Git-ref event source: no message body crosses Hermes MCP.

The authoritative source is a verified Git branch, NEVER append_store.
Every discovered MT commit is staged in a durable local outbox together with
its per-dialog cursor in ONE SQLite transaction, then passed to the existing
native metadata-only delivery mechanism. No Git writes occur in this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from . import DialogError, validate_message_id, validate_task_id
from .git_events import event_id, metadata, owner_digest, wire
from .github import sha

APPLICATION_ID = 0x47445036  # GDP6
EVENTS = ("source", "queue", "task_id", "head", "message_id", "seq", "from", "to")
# P32.3 migrated this historical routing alias away from the P26/P31
# notification journals. It is NOT an independently active conversation;
# the legacy registry nevertheless records it as active. Never scan its Git
# ref at startup or during polling. The P26 pilot is separately excluded by
# the explicit per-host exclude_task_id argument.
RETIRED_ROUTING_ANCHOR = "task_df06d95a56c65e80d8a2245386cb2e97"


class DirectGitJournal:
    """Private durable checkpoints and event queue, separate from legacy DB."""

    def __init__(self, path, registry, dialog, *, bootstrap=True,
                 exclude_task_id=None):
        path = Path(path)
        self.conn = None
        initially_missing = not (path.exists() or path.is_symlink())
        if initially_missing:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        st = path.lstat()
        # A previous aborted startup can leave an owned empty 0600 journal.
        # Initialize only truly empty files, never rewrite an existing schema.
        self._new = initially_missing or st.st_size == 0
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            raise DialogError("direct_event_store_rejected")
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA synchronous=FULL")
        try:
            with self.conn:
                app_id = self.conn.execute("PRAGMA application_id").fetchone()[0]
                tables = {r[0] for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if self._new:
                    if app_id != 0 or tables:
                        raise DialogError("direct_event_store_rejected")
                    self.conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    self.conn.execute("CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT)")
                    self.conn.execute("INSERT INTO owner VALUES(1,NULL)")
                    self.conn.execute("CREATE TABLE cursors (task_id TEXT PRIMARY KEY, head TEXT NOT NULL, seq INTEGER NOT NULL)")
                    self.conn.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, queued INTEGER NOT NULL DEFAULT 0 CHECK(queued IN (0,1)))")
                elif app_id != APPLICATION_ID or tables != {"owner", "cursors", "events"}:
                    raise DialogError("direct_event_store_rejected")
            row = self.conn.execute("SELECT digest FROM owner WHERE id=1").fetchone()
            if row is None or len(self.conn.execute("SELECT id FROM owner").fetchall()) != 1:
                raise DialogError("direct_event_store_rejected")
            self.owner_hash = row["digest"]
            if self.owner_hash is not None:
                if (type(self.owner_hash) is not str or len(self.owner_hash) != 64 or
                        any(c not in "0123456789abcdef" for c in self.owner_hash)):
                    raise DialogError("direct_event_store_rejected")
            # First activation is an explicit checkpoint of legacy history.
            # Later, new tasks (not in cursors) are scanned from seq=0.
            if self._new and bootstrap:
                self.bootstrap(registry, dialog, exclude_task_id=exclude_task_id)
        except BaseException:
            self.conn.close()
            raise

    def close(self):
        self.conn.close()

    def bind(self, digest):
        if (type(digest) is not str or len(digest) != 64 or
                any(c not in "0123456789abcdef" for c in digest) or
                self.owner_hash not in (None, digest)):
            raise DialogError("direct_event_owner_mismatch")
        with self.conn:
            self.conn.execute("UPDATE owner SET digest=? WHERE id=1 AND digest IS NULL", (digest,))
        self.owner_hash = digest

    def bootstrap(self, registry, dialog, *, exclude_task_id=None):
        """Checkpoint legacy tasks once. NO delivery blast after migration."""
        offset = 0
        while True:
            rows, next_offset = registry.page(offset=offset, limit=20)
            # Each page persists independently. A failed bootstrap must stop
            # activation, not silently ignore any uncheckpointed legacy task.
            for row in rows:
                if (row["state"] != "active" or row.get("remote_absent", 0) == 1 or
                        row["task_id"] in (exclude_task_id, RETIRED_ROUTING_ANCHOR)):
                    # Do not probe known-retired pilot, old routing alias, or
                    # explicit remote tombstones during initial migration.
                    continue
                try:
                    snap = dialog.load(row["task_id"])
                except DialogError as exc:
                    if exc.code == "branch_missing_or_inaccessible":
                        # A retired Git branch can outlive an 'active' local row.
                        # Never create a replacement or checkpoint a fake SHA.
                        continue
                    raise
                self.commit_batch(row["task_id"], snap.head, snap.state.seq, [])
            if next_offset is None:
                break
            offset = next_offset

    def cursor(self, task_id):
        validate_task_id(task_id)
        row = self.conn.execute(
            "SELECT head, seq FROM cursors WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return (sha(row["head"]), row["seq"])

    def commit_batch(self, task_id, head, seq, events):
        validate_task_id(task_id)
        sha(head)
        if type(seq) is not int or seq < 0 or type(events) is not list:
            raise DialogError("direct_event_cursor_rejected")
        with self.conn:
            before = self.conn.execute("SELECT seq FROM cursors WHERE task_id=?", (task_id,)).fetchone()
            if before is not None and before["seq"] > seq:
                raise DialogError("direct_event_cursor_rejected")
            for value in events:
                value = metadata(value, task_id)
                if value["seq"] > seq:
                    raise DialogError("direct_event_cursor_rejected")
                eid = event_id(value)
                saved = self.conn.execute(
                    "SELECT payload FROM events WHERE event_id=?", (eid,)).fetchone()
                encoded = wire(value)
                if saved is not None and saved["payload"] != encoded:
                    raise DialogError("direct_event_conflict")
                self.conn.execute("INSERT OR IGNORE INTO events(event_id,payload) VALUES (?,?)",
                                  (eid, encoded))
            self.conn.execute(
                "INSERT INTO cursors(task_id,head,seq) VALUES(?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET head=excluded.head, seq=excluded.seq",
                (task_id, head, seq))

    def pending(self):
        row = self.conn.execute(
            "SELECT event_id,payload FROM events WHERE queued=0 ORDER BY rowid LIMIT 1").fetchone()
        if row is None:
            return None
        value = json.loads(row["payload"])
        value = metadata(value, value.get("task_id"))
        if event_id(value) != row["event_id"]:
            raise DialogError("direct_event_store_rejected")
        return row["event_id"], value

    def acknowledge(self, eid):
        with self.conn:
            self.conn.execute("UPDATE events SET queued=1 WHERE event_id=?", (eid,))


def newly_published(dialog, snapshot, previous):
    """Return validated unseen MT event metadata and ensure Git ancestry.

    previous is (head,seq) or None. New dialogue starts from creation root.
    No guessed commit IDs and no events for return controls.
    """
    task_id = snapshot.state.task_id
    prior_seq = 0 if previous is None else previous[1]
    prior_head = None if previous is None else previous[0]
    if prior_seq > snapshot.state.seq or (prior_head and snapshot.head == prior_head and
                                          prior_seq != snapshot.state.seq):
        raise DialogError("direct_event_head_conflict")
    if prior_head == snapshot.head:
        return []
    unseen = snapshot.messages[prior_seq:]
    if not unseen and not prior_head:
        return []
    by_id = {m.message_id: m for m in unseen}
    commits = {}
    current = snapshot.head
    # At most one commit per message plus the branch-creation root.
    for _ in range(len(unseen) + 1):
        if current == prior_head:
            break
        obj = dialog._commit(current)
        label = obj.get("message")
        if type(label) is not str:
            raise DialogError("direct_event_commit_rejected")
        parents = obj.get("parents")
        if type(parents) is not list:
            raise DialogError("direct_event_commit_rejected")
        if label == f"git-dialog create {task_id}":
            # The immutable branch-creation root is NOT a message commit.
            # It is valid only when scanning a newly registered dialogue.
            if previous is not None or parents:
                raise DialogError("direct_event_commit_rejected")
        elif label.startswith(f"git-dialog {task_id} "):
            fields = label.split(" ")
            if len(fields) != 3 or fields[2] not in by_id or not parents:
                raise DialogError("direct_event_commit_rejected")
            if fields[2] in commits:
                raise DialogError("direct_event_commit_rejected")
            commits[fields[2]] = current
        else:
            raise DialogError("direct_event_commit_rejected")
        if not parents:
            current = None
            break
        if len(parents) != 1:
            raise DialogError("direct_event_commit_rejected")
        current = sha(parents[0].get("sha"))
    if ((prior_head is not None and current != prior_head) or
            (prior_head is None and current is not None) or
            any(m.message_id not in commits for m in unseen)):
        raise DialogError("direct_event_commit_rejected")
    events = []
    for msg in unseen:
        if msg.sender != "mt" or msg.kind == "return":
            continue
        commit_sha = commits[msg.message_id]
        # Validate the exact candidate's state; latest HEAD alone is not
        # proof that the message was present at this commit.
        state = dialog._snapshot(task_id, commit_sha)[0]
        if state.state.seq != msg.seq or state.last != msg:
            raise DialogError("direct_event_commit_rejected")
        events.append({"source": "git-dialog", "queue": "test", "task_id": task_id,
                       "head": commit_sha, "message_id": msg.message_id,
                       "seq": msg.seq, "from": "mt", "to": "chatgpt"})
    return events


class DirectGitNotificationPump:
    """Bounded Git scan; durable outbox; native metadata event delivery."""

    def __init__(self, journal, dialog, registry, delivery, *,
                 exclude_task_id=None, clock=time.monotonic):
        self.journal, self.dialog, self.registry, self.delivery = journal, dialog, registry, delivery
        self.exclude_task_id = exclude_task_id
        self.clock, self.next_scan, self.next_send = clock, 0, 0
        self.offset = 0
        self.inflight, self.error = None, None

    def scan(self):
        rows, next_offset = self.registry.page(offset=self.offset, limit=5)
        self.offset = 0 if next_offset is None else next_offset
        for row in rows:
            if (row["state"] != "active" or row.get("remote_absent", 0) == 1 or
                    row["task_id"] in (self.exclude_task_id, RETIRED_ROUTING_ANCHOR)):
                # Skip known deleted branches BEFORE any Git request.
                continue
            task_id = row["task_id"]
            before = self.journal.cursor(task_id)
            try:
                if before is not None and self.dialog._head(task_id) == before[0]:
                    # One Git ref GET; unchanged history need not be downloaded.
                    continue
                snapshot = self.dialog.load(task_id)
            except DialogError as exc:
                if exc.code == "branch_missing_or_inaccessible":
                    # Missing old branches must not block other live dialogues.
                    continue
                raise
            if before is not None and before == (snapshot.head, snapshot.state.seq):
                continue
            events = newly_published(self.dialog, snapshot, before)
            # Atomically commit both notifications and the new Git cursor.
            self.journal.commit_batch(task_id, snapshot.head, snapshot.state.seq, events)

    def tick(self):
        now = self.clock()
        if self.inflight is not None and self.inflight[1].done():
            eid, future = self.inflight
            try:
                if future.result() is True:
                    self.journal.acknowledge(eid)
                    self.error = None
                else:
                    self.error = "direct_event_delivery_unconfirmed"
            except Exception:
                self.error = "direct_event_delivery_unconfirmed"
            self.inflight = None
            self.next_send = now + 5
        if now >= self.next_scan:
            try:
                self.scan()
                self.next_scan = now + (5 if self.offset else 60)
            except Exception:
                self.error = "direct_event_reconciliation_unconfirmed"
                self.next_scan = now + 60
        if self.inflight is None and self.journal.owner_hash and now >= self.next_send:
            try:
                pending = self.journal.pending()
                if pending:
                    eid, value = pending
                    self.inflight = (eid, self.delivery.submit(eid, value, self.journal.owner_hash))
            except Exception:
                self.error = "direct_event_delivery_unconfirmed"
                self.next_send = now + 15

    def close(self):
        if self.inflight is not None:
            self.inflight[1].cancel()

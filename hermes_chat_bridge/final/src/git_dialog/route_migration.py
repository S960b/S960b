"""One-shot P32.3 recovery of historic P26/P31 routing, with NO Git operations.

Run only with BOTH hermes-bridge and git-dialog-taskboard STOPPED.
This is a fail-closed, locally invoked maintenance tool, not a server endpoint.
Never alter checkpoint, owner, events, or messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from .git_events import APPLICATION_ID as P26_APP, CHECKPOINT_HEAD, CHECKPOINT_SEQ
from .lifecycle import APPLICATION_ID as REGISTRY_APP
from .multi_git_events import APPLICATION_ID as P31_APP
from .preflight import TASK_ID
from .unix_authority import STATE

PILOT = "task_3c2d37a2485b"
MISTAKEN_ANCHOR = "task_df06d95a56c65e80d8a2245386cb2e97"
P31_CHECKPOINT = 19
CONFIRM = "RESTORE_HISTORICAL_PILOT_P323"


class RouteMigrationError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise RouteMigrationError(code)


def safe_file(path):
    path = Path(path)
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
            stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1,
            "unsafe_database_file")
    return path


def safe_dir(path):
    path = Path(path)
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and
            stat.S_IMODE(st.st_mode) == 0o700,
            "unsafe_backup_directory")
    return path


def connection(path, readonly=False):
    safe_file(path)
    conn = sqlite3.connect(path, timeout=2)
    conn.row_factory = sqlite3.Row
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


def event_digest(conn, prefix):
    rows = conn.execute("SELECT event_id,payload,queued FROM " + prefix +
                        ".events ORDER BY event_id").fetchall()
    h = hashlib.sha256()
    pending = 0
    for row in rows:
        h.update(json.dumps(tuple(row), ensure_ascii=True,
                            separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
        if row["queued"] == 0:
            pending += 1
    return (len(rows), pending, h.digest())


def check_state(state):
    require(TASK_ID == PILOT, "source_pilot_binding_mismatch")
    registry = connection(state / "lifecycle.p21.sqlite", readonly=True)
    try:
        require(registry.execute("PRAGMA application_id").fetchone()[0] ==
                REGISTRY_APP, "wrong_registry")
        old = registry.execute(
            "SELECT state,remote_absent FROM dialogs WHERE task_id=?",
            (PILOT,)).fetchone()
        new = registry.execute(
            "SELECT state FROM dialogs WHERE task_id=?",
            (MISTAKEN_ANCHOR,)).fetchone()
        require(old is not None and tuple(old) == ("tombstoned", 1),
                "legacy_pilot_not_confirmed_deleted")
        require(new is not None and new["state"] == "active",
                "migration_anchor_not_active")
    finally:
        registry.close()


def check_journals(conn, *, migrated=False):
    require(conn.execute("PRAGMA main.application_id").fetchone()[0] ==
            P26_APP, "wrong_p26_database")
    require(conn.execute("PRAGMA p31.application_id").fetchone()[0] ==
            P31_APP, "wrong_p31_database")
    for schema in ("main", "p31"):
        require(conn.execute("PRAGMA " + schema +
                             ".integrity_check").fetchone()[0] == "ok",
                "database_integrity_failed")
        require(conn.execute("PRAGMA " + schema +
                             ".journal_mode").fetchone()[0].lower() == "delete",
                "atomic_rollback_journal_required")
    p26 = conn.execute("SELECT task,head,seq,owner_hash FROM main.route").fetchall()
    p31 = conn.execute("SELECT pilot,checkpoint,owner_hash FROM p31.route WHERE id=1").fetchall()
    require(len(p26) == 1 and len(p31) == 1, "route_row_count_changed")
    expected = PILOT if migrated else MISTAKEN_ANCHOR
    require(p26[0]["task"] == expected and p26[0]["head"] == CHECKPOINT_HEAD and
            p26[0]["seq"] == CHECKPOINT_SEQ, "p26_route_mismatch")
    require(p31[0]["pilot"] == expected and
            p31[0]["checkpoint"] == P31_CHECKPOINT, "p31_route_mismatch")
    owner26, owner31 = p26[0]["owner_hash"], p31[0]["owner_hash"]
    require(owner26 == owner31 and
            (owner26 is None or
             (isinstance(owner26, str) and len(owner26) == 64 and
              all(c in "0123456789abcdef" for c in owner26))),
            "event_owner_binding_mismatch")
    digests = (event_digest(conn, "main"), event_digest(conn, "p31"))
    require(digests[0][1] == digests[1][1] == 0,
            "pending_event_requires_manual_review")
    return digests


def backup(source, destination):
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                 0o600)
    os.close(fd)
    src = connection(source, readonly=True)
    dest = connection(destination)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    with open(destination, "rb") as fh:
        os.fsync(fh.fileno())


def migrate(state, *, apply=False, backup_dir=None):
    state = Path(state)
    check_state(state)
    p26 = safe_file(state / "authority-events.p26.sqlite")
    p31 = safe_file(state / "authority-events.p31.sqlite")
    conn = connection(p26)
    try:
        conn.execute("ATTACH DATABASE ? AS p31", (str(p31),))
        # A multi-file SQLite transaction is atomic in rollback-journal DELETE
        # mode; refuse WAL or other journal modes instead of changing settings.
        before = check_journals(conn)
        if not apply:
            return {"status": "ready", "p26_events": before[0][0],
                    "p31_events": before[1][0], "pending": 0}
    finally:
        conn.close()

    backups = safe_dir(backup_dir if backup_dir is not None else state / "backups")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    b26 = backups / ("p323_p26_" + stamp + ".sqlite")
    b31 = backups / ("p323_p31_" + stamp + ".sqlite")
    # Never touch live DBs until BOTH full backups were durably written and checked.
    backup(p26, b26)
    backup(p31, b31)
    for source, copy in ((p26, b26), (p31, b31)):
        src = connection(source, readonly=True)
        dst = connection(copy, readonly=True)
        try:
            require(src.execute("PRAGMA application_id").fetchone()[0] ==
                    dst.execute("PRAGMA application_id").fetchone()[0],
                    "backup_application_id_changed")
            require(src.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    and dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                    "backup_integrity_failed")
        finally:
            src.close()
            dst.close()
    dfd = os.open(backups, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)

    conn = connection(p26)
    try:
        conn.execute("ATTACH DATABASE ? AS p31", (str(p31),))
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA p31.synchronous=FULL")
        conn.execute("BEGIN EXCLUSIVE")
        try:
            again = check_journals(conn)
            require(again == before, "event_journal_changed_during_backup")
            a = conn.execute("UPDATE main.route SET task=? WHERE task=?",
                             (PILOT, MISTAKEN_ANCHOR))
            b = conn.execute("UPDATE p31.route SET pilot=? WHERE id=1 AND pilot=?",
                             (PILOT, MISTAKEN_ANCHOR))
            require(a.rowcount == b.rowcount == 1, "route_update_not_unique")
            require(check_journals(conn, migrated=True) == before,
                    "event_journal_changed_during_migration")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        require(check_journals(conn, migrated=True) == before,
                "post_commit_verification_failed")
    finally:
        conn.close()
    return {"status": "migrated", "p26_events": before[0][0],
            "p31_events": before[1][0], "pending": 0,
            "backup_names": [b26.name, b31.name]}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["plan"]:
        action = False
    elif argv == ["apply", CONFIRM]:
        action = True
    else:
        print(json.dumps({"error": "explicit_confirmation_required"}))
        return 2
    try:
        print(json.dumps(migrate(STATE, apply=action), sort_keys=True))
        return 0
    except (RouteMigrationError, OSError, sqlite3.Error) as exc:
        # No SQL, payload, credentials or arbitrary exception text on stdout.
        code = str(exc) if isinstance(exc, RouteMigrationError) else "migration_unavailable"
        print(json.dumps({"error": code}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

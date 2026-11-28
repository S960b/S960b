"""P36.1 shared, private GitHub REST response cache and rate-limit guard.

One SQLite file is shared by gateway, MT CLI and optional taskboard. Contents
never leave the owner machine. A cached mutable response is returned ONLY after
an authenticated GitHub 304. Immutable Git SHA objects may be reused after
a fresh repo/ref access check performed by GitHubDialog. No request retries.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import time

from . import DialogError

APPLICATION_ID = 0x47425131
DEFAULT_CACHE = Path("/home/kali/agent-dialog-beta/state/github-api-cache.p361.sqlite")
RESERVE = 150
MAX_CACHED = 500
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_BYTES = 2 * 1024 * 1024
# We cache immutable Git objects, never an arbitrary dynamic API result.
_SHA_OBJECT = __import__("re").compile(
    r"^/repos/S960b/agent-dialogs/git/(?:commits|blobs)/[0-9a-f]{40}$"
    r"|^/repos/S960b/agent-dialogs/git/trees/[0-9a-f]{40}\?recursive=1$")
_MUTABLE = __import__("re").compile(
    r"^/repos/S960b/agent-dialogs$"
    r"|^/repos/S960b/agent-dialogs/git/ref/heads/dialogs/task_[0-9a-f]{32}$")


def _secure_db(path):
    path = Path(path)
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or
            stat.S_IMODE(parent.st_mode) != 0o700):
        raise DialogError("github_cache_directory_rejected")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    node = path.lstat()
    if (not stat.S_ISREG(node.st_mode) or node.st_uid != os.getuid() or
            stat.S_IMODE(node.st_mode) != 0o600 or node.st_nlink != 1):
        raise DialogError("github_cache_file_rejected")
    return path


def _connect(path):
    db = sqlite3.connect(_secure_db(path), timeout=5)
    try:
        db.row_factory = sqlite3.Row
        # Atomic schema bootstrap across the bridge, agent and taskboard.
        # Avoid first-start races that could otherwise fail the new service.
        db.execute("BEGIN IMMEDIATE")
        app = db.execute("PRAGMA application_id").fetchone()[0]
        names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if app == 0 and not names:
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("CREATE TABLE quota (owner TEXT PRIMARY KEY, remaining INTEGER,"
                       " maximum INTEGER, reset_epoch INTEGER, blocked_until INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE responses (owner TEXT NOT NULL, path TEXT NOT NULL,"
                       " etag TEXT, data TEXT NOT NULL, access_epoch INTEGER NOT NULL,"
                       " PRIMARY KEY(owner,path))")
        elif app != APPLICATION_ID or names != {"quota", "responses"}:
            raise DialogError("github_cache_schema_rejected")
        db.commit()
        return db
    except BaseException:
        db.rollback()
        db.close()
        raise


def _immutable(path):
    return bool(_SHA_OBJECT.fullmatch(path))


def _mutable(path):
    return bool(_MUTABLE.fullmatch(path))


class SharedApiBudget:
    def __init__(self, path, token, *, clock=time.time):
        if type(token) is not str or not token:
            raise DialogError("authorization_required")
        self.path = Path(path)
        self.owner = hashlib.sha256(("git-dialog-credential\\0" + token).encode("ascii")).hexdigest()
        self.clock = clock
        # Fail closed on an invalid/foreign existing cache; no chmod/reset.
        with closing(_connect(self.path)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("SELECT 1 FROM quota LIMIT 1").fetchone()

    def cached(self, path):
        if not (_immutable(path) or _mutable(path)):
            return None
        with closing(_connect(self.path)) as db:
            row = db.execute(
                "SELECT etag,data FROM responses WHERE owner=? AND path=?",
                (self.owner, path)).fetchone()
        if row is None or len(row["data"]) > MAX_BYTES:
            return None
        # Never trust responses originating in the DB without validating their
        # JSON structure and SHA linkage in GitHubDialog.
        return row["etag"], row["data"]

    def gate(self):
        now = int(self.clock())
        with closing(_connect(self.path)) as db:
            row = db.execute("SELECT remaining,reset_epoch,blocked_until FROM quota "
                             "WHERE owner=?", (self.owner,)).fetchone()
        if row is None:
            return
        if row["blocked_until"] > now:
            raise DialogError("github_rate_limited")
        if (row["remaining"] is not None and row["remaining"] <= RESERVE and
                row["reset_epoch"] is not None and row["reset_epoch"] > now):
            raise DialogError("github_rate_limited")

    def observe(self, headers, *, status, path=None, data=None):
        """Use only verified HTTP headers. No headers => no quota deductions."""
        def val(key):
            try:
                item = headers.get(key)
                return int(item) if item is not None else None
            except (TypeError, ValueError, OverflowError):
                return None
        now = int(self.clock())
        remaining, maximum, reset = (
            val("x-ratelimit-remaining"), val("x-ratelimit-limit"),
            val("x-ratelimit-reset"))
        retry = val("retry-after")
        block = 0
        if status in (403, 429):
            if retry is not None and 0 <= retry <= 86400:
                block = now + retry
            elif remaining == 0 and reset is not None and reset > now:
                block = reset + 1
            elif status == 429:
                block = now + 60
        with closing(_connect(self.path)) as db, db:
            if remaining is not None and maximum is not None and reset is not None:
                if (0 <= remaining <= maximum <= 1000000 and
                        now - 3600 <= reset <= now + 86400):
                    db.execute("INSERT INTO quota(owner,remaining,maximum,reset_epoch,blocked_until)"
                               " VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET "
                               "remaining=CASE WHEN quota.reset_epoch=excluded.reset_epoch "
                               "THEN MIN(quota.remaining,excluded.remaining) "
                               "ELSE excluded.remaining END,"
                               "maximum=excluded.maximum,reset_epoch=excluded.reset_epoch,"
                               "blocked_until=MAX(quota.blocked_until,excluded.blocked_until)",
                               (self.owner, remaining, maximum, reset, block))
            elif block:
                db.execute("INSERT INTO quota(owner,blocked_until) VALUES(?,?) "
                           "ON CONFLICT(owner) DO UPDATE SET "
                           "blocked_until=MAX(quota.blocked_until,excluded.blocked_until)",
                           (self.owner, block))
            if (status in (200, 201) and path is not None and data is not None and
                    (_immutable(path) or _mutable(path)) and
                    type(data) is str and len(data) <= MAX_BYTES):
                etag = headers.get("etag")
                if type(etag) is not str or not 1 <= len(etag) <= 250 or any(
                        ord(ch) < 32 or ord(ch) > 126 for ch in etag):
                    etag = None
                db.execute("INSERT INTO responses(owner,path,etag,data,access_epoch)"
                           " VALUES(?,?,?,?,?) ON CONFLICT(owner,path) DO UPDATE SET "
                           "etag=excluded.etag,data=excluded.data,access_epoch=excluded.access_epoch",
                           (self.owner, path, etag, data, now))
                count, used = db.execute(
                    "SELECT COUNT(*),COALESCE(SUM(LENGTH(data)),0) FROM responses WHERE owner=?",
                    (self.owner,)).fetchone()
                while count > MAX_CACHED or used > MAX_CACHE_BYTES:
                    old = db.execute(
                        "SELECT path,LENGTH(data) FROM responses WHERE owner=?"
                        " ORDER BY access_epoch,rowid LIMIT 1", (self.owner,)).fetchone()
                    if old is None: break
                    db.execute("DELETE FROM responses WHERE owner=? AND path=?",
                               (self.owner, old["path"]))
                    count -= 1
                    used -= old[1]

    def status(self):
        now = int(self.clock())
        with closing(_connect(self.path)) as db:
            row = db.execute("SELECT remaining,maximum,reset_epoch,blocked_until FROM quota "
                             "WHERE owner=?", (self.owner,)).fetchone()
        if row is None:
            return {"source": "unavailable", "remaining": None, "limit": None,
                    "reset_epoch": None, "paused": False}
        return {"source": "github_headers",
                "remaining": row["remaining"], "limit": row["maximum"],
                "reset_epoch": row["reset_epoch"],
                "paused": row["blocked_until"] > now or
                          (row["remaining"] is not None and row["remaining"] <= RESERVE and
                           row["reset_epoch"] is not None and row["reset_epoch"] > now)}


def shared_status(path=DEFAULT_CACHE):
    """Read-only diagnostic. NEVER create a DB or leak credential identity."""
    try:
        path = Path(path)
        if not path.exists() or path.is_symlink():
            raise ValueError()
        st = path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600):
            raise ValueError()
        db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT remaining,maximum,reset_epoch,blocked_until FROM quota").fetchall()
        finally:
            db.close()
        if not rows:
            raise ValueError()
        # Multiple local PATs can share one account-level GitHub quota. Show
        # the most conservative observed balance without exposing identities.
        usable = [r for r in rows if r[0] is not None and r[1] is not None]
        if not usable:
            raise ValueError()
        remaining, limit, reset, _ = min(usable, key=lambda r: r[0])
        blocked = max(row[3] for row in rows)
        now = int(time.time())
        return {"source": "github_headers", "remaining": remaining,
                "limit": limit, "reset_epoch": reset,
                "paused": bool(blocked > now or (remaining is not None and
                           remaining <= RESERVE and reset is not None and reset > now))}
    except (ValueError, OSError, sqlite3.Error):
        return {"source": "unavailable", "remaining": None, "limit": None,
                "reset_epoch": None, "paused": False}

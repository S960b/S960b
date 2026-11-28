"""P36 local notification of an already-verified Git commit to MT plugin.

No message body, code, secrets, network or Git writes. The receiving plugin
watches the fixed 0700 directory; its fallback Git polling handles lost wakes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat

from . import validate_message_id, validate_task_id
from .github import sha

DEFAULT_DIR = Path("/home/kali/.local/state/mt-git-inbox")
NAME = "p36-notice.json"


def notify_plugin(receipt, directory=DEFAULT_DIR):
    """Best-effort metadata-only wakeup, independently safe to lose.

    Caller MUST have verified Git commit reachability and owner identity.
    Failure does not change the publication or authorize a Git retry.
    """
    if type(receipt) is not dict or set(receipt) != {
            "task_id", "head", "message_id", "seq", "observed"}:
        return False
    validate_task_id(receipt["task_id"])
    validate_message_id(receipt["message_id"])
    sha(receipt["head"])
    if type(receipt["seq"]) is not int or receipt["seq"] < 1:
        return False
    directory = Path(directory)
    try:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o700):
            return False
        payload = json.dumps(
            {k: receipt[k] for k in ("task_id", "head", "message_id", "seq")},
            sort_keys=True, separators=(",", ":")).encode("ascii")
        name = ".p36-notice-" + secrets.token_hex(8)
        target = directory / NAME
        temp = directory / name
        fd = None
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.write(fd, payload)
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.replace(temp, target)
            dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if fd is not None:
                os.close(fd)
            if temp.exists():
                try: temp.unlink()
                except OSError: pass
        return True
    except (OSError, ValueError, TypeError):
        return False

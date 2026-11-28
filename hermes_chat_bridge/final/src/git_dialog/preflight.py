"""One-shot LIVE read-only preflight. No branch lifecycle or publication.

Run locally: PYTHONPATH=src python -m git_dialog.preflight CONFIGURED_TOKEN_FILE
The path is local configuration, never a message or a discovered credential.
Do not run under shell tracing; stdout is a bounded nonsecret JSON summary.
"""
from __future__ import annotations

import json
import os
import stat
import sys

from . import DialogError
from .github import ApiError, GitHubDialog, JsonHttp, sha

REPOSITORY = "S960b/agent-dialogs"
TASK_ID = "task_3c2d37a2485b"
MAIN_SHA = "38ed706d21a0ca7520271e561e4406f777601261"
BASE = "/repos/" + REPOSITORY


class ReadOnlyTransport:
    def __init__(self, transport):
        self._transport = transport
        self.requests = 0

    def request(self, method, path, payload=None):
        if method != "GET" or payload is not None:
            raise DialogError("preflight_write_forbidden")
        if path != BASE and not path.startswith(BASE + "/git/"):
            raise DialogError("preflight_scope_forbidden")
        self.requests += 1
        return self._transport.request(method, path, payload)


def read_configured_token(path):
    """Read ONLY caller's explicitly configured file; never search/chmod it."""
    fd = None
    try:
        # Linux local runner only. Fail closed when NOFOLLOW is unavailable.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or
                info.st_uid != os.getuid() or not 1 <= info.st_size <= 4096):
            raise DialogError("credential_file_rejected")
        raw = os.read(fd, 4097)
        if len(raw) > 4096:
            raise DialogError("credential_file_rejected")
        token = raw.decode("ascii").rstrip("\r\n")
        if not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise DialogError("credential_file_rejected")
        return token
    except (OSError, ValueError, UnicodeError, AttributeError):
        raise DialogError("credential_file_rejected") from None
    finally:
        if fd is not None:
            os.close(fd)


def run_preflight(transport):
    ro = ReadOnlyTransport(transport)
    dialog = GitHubDialog(ro, REPOSITORY)
    dialog._private()

    def main_head():
        obj = ro.request("GET", BASE + "/git/ref/heads/main")
        if obj.get("ref") != "refs/heads/main" or obj.get("object", {}).get("type") != "commit":
            raise DialogError("invalid_main_ref")
        return sha(obj["object"].get("sha"))

    before = main_head()
    if before != MAIN_SHA:
        raise DialogError("main_checkpoint_changed")
    try:
        snapshot = dialog.load(TASK_ID)
    except DialogError as exc:
        if exc.code != "branch_missing_or_inaccessible":
            raise
        result = {"dialog": "missing_or_inaccessible",
                  "publication": "blocked_requires_explicit_branch_creation"}
    else:
        result = {"dialog": "valid_snapshot", "head": snapshot.head,
                  "seq": snapshot.state.seq, "status": snapshot.state.status,
                  "publication": "not_attempted"}
    if main_head() != before:
        raise DialogError("main_changed_during_preflight")
    return {"repository": REPOSITORY, "task_id": TASK_ID, "private": True,
            "main_sha": before, "requests": ro.requests, "mutations": 0,
            "read_transport": "passed", "write_transport": "not_tested", **result}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('{"error":"expected_one_locally_configured_credential_file"}')
        return 2
    try:
        token = read_configured_token(args[0])
        transport = JsonHttp(token)
        del token
        result = run_preflight(transport)
    except ApiError as exc:
        result = {"error": "github_http_error", "status": exc.status}
    except DialogError as exc:
        # Use the machine code only; detail may contain remote payload data.
        result = {"error": exc.code}
    except Exception:
        # Never expose a traceback, local path, raw HTTP body or token.
        result = {"error": "preflight_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())

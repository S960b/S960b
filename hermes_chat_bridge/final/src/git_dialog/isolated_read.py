"""Fixed read-only Git snapshot in an owned, deadline-bounded child process.

No shell, callbacks, secret argv/env, credential discovery or inherited proxies.
Only this process's own child may be terminated. No remote event is executed.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from . import DialogError, parse_snapshot
from .preflight import TASK_ID

MAX_WIRE = 16 * 1024 * 1024


def pack(snapshot):
    if snapshot is None: return None
    return {"task": snapshot.task, "state": snapshot.state.to_dict(),
            "messages": [m.to_dict() for m in snapshot.messages], "head": snapshot.head}


def unpack(obj):
    if type(obj) is not dict or set(obj) != {"task", "state", "messages", "head"}:
        raise DialogError("poll_child_protocol")
    result = parse_snapshot(obj["task"], obj["state"], obj["messages"], obj["head"])
    if result.state.task_id != TASK_ID or not re.fullmatch("[0-9a-f]{40}", result.head):
        raise DialogError("poll_child_scope")
    return result


def _dispose(process):
    try:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None: stream.close()


class IsolatedRead:
    def __init__(self, token, *, budget=15):
        if not isinstance(token, str) or not token or any(not 33 <= ord(c) <= 126 for c in token):
            raise DialogError("authorization_required")
        if type(budget) not in (int, float) or not 1 <= budget <= 30:
            raise DialogError("poll_budget_rejected")
        self._token, self._budget = token, budget
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def cancel(self):
        # Non-blocking even while the reader holds its refresh lock.
        self._stop.set()

    def read(self, previous):
        with self._lock:
            if self._stop.is_set(): raise DialogError("poll_cancelled")
            wire = json.dumps({"token": self._token, "previous": pack(previous)},
                              ensure_ascii=False, allow_nan=False).encode()
            if len(wire) > MAX_WIRE: raise DialogError("poll_child_size")
            deadline = time.monotonic() + self._budget
            # Fixed executable/module, sanitized env, no shell or external paths.
            process = subprocess.Popen([sys.executable, "-m", "git_dialog.isolated_read"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent), "LANG": "C.UTF-8"},
                close_fds=True)
            try:
                first = True
                while True:
                    if self._stop.is_set(): raise DialogError("poll_cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise DialogError("poll_deadline")
                    try:
                        output, _ = process.communicate(input=wire if first else None,
                                                         timeout=min(0.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        first = False
                if self._stop.is_set(): raise DialogError("poll_cancelled")
                if time.monotonic() > deadline: raise DialogError("poll_deadline")
                if process.returncode != 0 or len(output) > MAX_WIRE:
                    raise DialogError("poll_child_failed")
                result = json.loads(output)
                if type(result) is not dict or set(result) != {"snapshot", "error"}:
                    raise DialogError("poll_child_protocol")
                if result["error"] is not None:
                    # Never echo a remote body, token, path or exception text.
                    raise DialogError("poll_child_source_error")
                snapshot = unpack(result["snapshot"])
                if previous and (snapshot.task != previous.task or
                        snapshot.messages[:len(previous.messages)] != previous.messages):
                    raise DialogError("poll_history_changed")
                return snapshot
            except (ValueError, TypeError, KeyError):
                raise DialogError("poll_child_protocol") from None
            finally:
                _dispose(process)


def child():
    # All input is local IPC from the fixed parent, not a job/action protocol.
    try:
        raw = sys.stdin.buffer.read(MAX_WIRE + 1)
        if len(raw) > MAX_WIRE: raise DialogError("poll_child_size")
        obj = json.loads(raw)
        if type(obj) is not dict or set(obj) != {"token", "previous"}:
            raise DialogError("poll_child_protocol")
        previous = None if obj["previous"] is None else unpack(obj["previous"])
        from .github import JsonHttp
        from .service import SnapshotReader
        reader = SnapshotReader(JsonHttp(obj["token"], timeout=3))
        reader._snapshot = previous
        reader.refresh()  # Existing private/main/fullhash/history/head checks, GET-only.
        if reader.status()["error"]: raise DialogError("poll_child_source_error")
        result = {"snapshot": pack(reader.current()), "error": None}
        wire = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(wire) > MAX_WIRE: raise DialogError("poll_child_size")
        sys.stdout.buffer.write(wire)
        return 0
    except Exception:
        sys.stdout.write('{"snapshot":null,"error":"poll_child_source_error"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(child())

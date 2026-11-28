"""Bounded synthetic runtime of the actual candidate runner, main-thread SIGTERM.

No operator paths, ports, keys or credentials accepted. Not deployment.
"""
import argparse
import base64
import hashlib
import hmac
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets
import signal
import tempfile
import threading
import time

from . import DialogError
from .runner import _RuntimeProbe, serve_candidate
from .service import AuthAuthority, PanelApplication, SnapshotReader, initialize
from .wakeup_host import WakeupHost
from .wakeup_smoke import fixture_event


def _cases(enabled, basic, key):
    raw = json.dumps(fixture_event(), sort_keys=True).encode()
    signed = {"X-Hermes-Signature": "sha256=" + hmac.new(key, raw, hashlib.sha256).hexdigest()}
    owner = {"Authorization": basic}
    cases = [("GET", "/api/status", None, {}, 401),
             ("GET", "/api/status", None, owner, 200)]
    if not enabled:
        return cases + [("POST", "/bridge/wakeup", raw, signed, 404)]
    return cases + [("POST", "/bridge/wakeup", raw, owner, 401),
                    ("GET", "/api/status", None, signed, 401),
                    ("POST", "/bridge/wakeup", raw, signed, 202),
                    ("DELETE", "/", None, owner, 405)]


def _phase(directory, password, enabled):
    # Publicly non-reusable random test material only; never printed or saved.
    key = secrets.token_bytes(32)
    host = WakeupHost(PanelApplication(AuthAuthority(directory), SnapshotReader(), 8790),
                      key=key if enabled else None)
    probe = _RuntimeProbe()
    finished, client_failed = threading.Event(), threading.Event()
    statuses = []
    basic = "Basic " + base64.b64encode(("owner:" + password).encode()).decode()
    cases = _cases(enabled, basic, key)
    previous = signal.getsignal(signal.SIGTERM)

    def client():
        # Never signal before the runner's handler exists or after it is restored.
        if not probe.ready.wait(2):
            client_failed.set()
            return
        try:
            deadline = time.monotonic() + 5
            for method, path, body, headers, expected in cases:
                if time.monotonic() >= deadline:
                    raise DialogError("runner_runtime_client_deadline")
                conn = HTTPConnection("127.0.0.1", probe.port, timeout=1)
                try:
                    conn.request(method, path, body, {"Connection": "close",
                        "Content-Type": "application/json", **headers})
                    response = conn.getresponse()
                    data = response.read(262145)
                    if (response.status != expected or len(data) > 262144 or
                            response.getheader("Cache-Control") != "no-store"):
                        raise DialogError("runner_runtime_http_failed")
                    if path == "/api/status" and expected == 200:
                        status = json.loads(data)
                        if (status["mode"] != "synthetic" or not status["read_only"] or
                                status["delete_enabled"] or status["error"] is not None):
                            raise DialogError("runner_runtime_source_failed")
                    if expected == 202 and json.loads(data)["status"] != "scheduled":
                        raise DialogError("runner_runtime_schedule_failed")
                    statuses.append(response.status)
                finally:
                    conn.close()
            deadline = time.monotonic() + 1
            while probe.counts["wakeup"] != int(enabled) and time.monotonic() < deadline:
                finished.wait(0.01)
            if probe.counts != {"periodic": 1, "wakeup": int(enabled)}:
                raise DialogError("runner_runtime_reads_failed")
        except Exception:
            client_failed.set()
        finally:
            # This self-test owns its own PID; never targets services or children.
            if not finished.is_set(): probe.terminate_self()

    thread = threading.Thread(target=client, name="candidate-runtime-client", daemon=True)
    thread.start()
    try:
        serve_candidate(host, 0, _probe=probe)
    finally:
        finished.set()
        thread.join(timeout=3)
    if (thread.is_alive() or client_failed.is_set() or not probe.closed or
            signal.getsignal(signal.SIGTERM) != previous):
        raise DialogError("runner_runtime_failed")
    return {"http_statuses": statuses, "reader_attempts": probe.counts,
            "sigterm": "handled_on_main_thread", "shutdown": "threads_joined_sockets_closed"}


def run():
    if threading.current_thread() is not threading.main_thread():
        raise DialogError("candidate_main_thread_required")
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="git-dialog-runner-runtime-") as temp:
        password = secrets.token_urlsafe(24)
        directory = Path(temp) / "state"
        initialize(directory, password)
        off = _phase(directory, password, False)
        on = _phase(directory, password, True)
    if time.monotonic() - start > 22:
        raise DialogError("runner_runtime_deadline_failed")
    return {"status": "passed", "entrypoint": "serve_candidate", "default_off": off,
            "test_opt_in": on, "source": "synthetic", "temporary_database": True,
            "ephemeral_test_key": True, "real_data_git_credentials_used": False,
            "old_services_changed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded candidate runtime self-test only")
    parser.add_argument("--self-test", required=True, action="store_true")
    parser.parse_args(argv)
    try:
        result = run()
    except DialogError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "runner_runtime_failed"}
    print(json.dumps(result, sort_keys=True))
    return int("error" in result)


if __name__ == "__main__":
    raise SystemExit(main())

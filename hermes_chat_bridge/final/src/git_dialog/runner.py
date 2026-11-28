"""Standalone candidate: separate state/port, synthetic/default wakeup-off.

No bridge forwarding. Explicit key-init provisions a new dedicated beta key;
serve never creates a key. No secret bytes in argv/env/log or JSON output.
"""
import argparse
import fcntl
import json
import os
import signal
import threading

from . import DialogError
from .service import (AuthAuthority, PanelApplication, SnapshotReader, initialize,
                      _interactive_secret, _private_dir, _private_file)
from .wakeup_host import WakeupHost
from .wakeup_smoke import StrictChannel, close_owned


class _RuntimeProbe:
    """Private, in-memory self-test handshake; never supplied by HTTP data."""
    def __init__(self):
        self.ready = threading.Event()
        self.port = None
        self.counts = {"periodic": 0, "wakeup": 0}
        self.closed = False
        self.signal_lock = threading.Lock()
        self.signal_active = False

    def terminate_self(self):
        with self.signal_lock:
            if self.signal_active:
                os.kill(os.getpid(), signal.SIGTERM)


def serve_candidate(host, port, *, _probe=None):
    if threading.current_thread() is not threading.main_thread():
        raise DialogError("candidate_main_thread_required")
    if (_probe is not None and type(_probe) is not _RuntimeProbe) or (
            type(port) is not int or port in (8787, 8789) or
            not (1024 < port <= 65535 or (port == 0 and _probe is not None))):
        raise DialogError("candidate_port_rejected")
    if type(host) is not WakeupHost or host.loop._reader.transport is not None:
        raise DialogError("candidate_synthetic_required")
    from waitress.server import create_server
    stop, failed = threading.Event(), threading.Event()
    channels = {}
    gateway = host._application.panel.gateway is not None
    server = create_server(host, host="127.0.0.1", port=port, map=channels,
        threads=2, connection_limit=16, channel_timeout=150 if gateway else 2, cleanup_interval=1,
        max_request_header_size=8192, max_request_body_size=8192 if gateway else 4096,
        expose_tracebacks=False, log_socket_errors=False, clear_untrusted_proxy_headers=True)
    server.channel_class = StrictChannel
    def reader_loop():
        try:
            while not stop.is_set():
                result = host.loop.tick()
                if _probe is not None and result["refreshed"]:
                    _probe.counts[result["reason"]] += 1
                stop.wait(0.1)
        except Exception:
            failed.set(); stop.set(); close_owned(channels)
    worker = threading.Thread(target=reader_loop, name="candidate-synthetic-reader", daemon=True)
    previous = signal.getsignal(signal.SIGTERM)
    shutting_down = False
    def terminate(*args):
        if not shutting_down: raise KeyboardInterrupt()
    watchdog = None
    try:
        if _probe is not None:
            effective = int(server.effective_port)
            if effective <= 1024 or effective in (8787, 8789):
                raise DialogError("candidate_port_rejected")
            host._application.panel.host = "127.0.0.1:" + str(effective)
            _probe.port = effective
            def expired():
                failed.set(); stop.set(); close_owned(channels)
            watchdog = threading.Timer(10, expired)
            watchdog.daemon = True
        signal.signal(signal.SIGTERM, terminate)
        if _probe is not None: _probe.signal_active = True
        worker.start()
        if watchdog is not None:
            watchdog.start()
            _probe.ready.set()  # Signal handler installed BEFORE the test client can signal.
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        shutting_down = True  # Further own-test signals cannot interrupt cleanup.
        stop.set(); close_owned(channels)
        host.loop._reader.cancel()
        if worker.ident is not None: worker.join(timeout=2)
        host.loop.stop()
        server.task_dispatcher.shutdown(timeout=2)
        if watchdog is not None:
            watchdog.cancel()
            if watchdog.ident is not None: watchdog.join(timeout=1)
        if _probe is not None:
            with _probe.signal_lock:
                _probe.signal_active = False
                signal.signal(signal.SIGTERM, previous)
        else:
            signal.signal(signal.SIGTERM, previous)
    if worker.is_alive() or channels or server.task_dispatcher.threads or failed.is_set():
        raise DialogError("candidate_shutdown_failed")
    if _probe is not None: _probe.closed = True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate authenticated read-only beta runner")
    parser.add_argument("action", choices=("init", "serve", "key-init"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--enable-wakeup", action="store_true")
    parser.add_argument("--mode", choices=("synthetic", "github", "gateway"), default="synthetic")
    args = parser.parse_args(argv)
    lock = None
    gateway_lock = None
    try:
        if not 1024 < args.port <= 65535 or args.port in (8787, 8789):
            raise DialogError("candidate_port_rejected")
        if args.action == "init":
            if args.enable_wakeup or args.mode != "synthetic": raise DialogError("candidate_init_option_rejected")
            password = _interactive_secret("Новый пароль отдельной тестовой панели: ")
            if password != _interactive_secret("Повторите пароль: "):
                raise DialogError("password_mismatch")
            initialize(args.state_dir, password)
            marker = _private_dir(args.state_dir) / "runner-candidate.marker"
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            print('{"status":"initialized","source":"synthetic"}')
            return 0
        directory = _private_dir(args.state_dir)
        if args.mode == 'gateway' and (args.action != 'serve' or args.enable_wakeup):
            raise DialogError('gateway_panel_options_rejected')
        if args.mode == 'gateway' and args.port != 8791:
            raise DialogError('gateway_panel_port_rejected')
        _private_file(directory / "runner-candidate.marker")  # Existing P1 state is not opted in.
        authority = AuthAuthority(directory)
        lock_path = directory / ('panel-gateway.p24.lock' if args.mode=='gateway' else 'server.lock')
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        if args.mode == 'gateway': flags |= os.O_EXCL
        lock = os.open(lock_path, flags, 0o600)
        if args.mode == 'gateway':
            info = os.fstat(lock)
            gateway_lock = (lock_path,info.st_dev,info.st_ino)
        _private_file(lock_path)
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise DialogError("candidate_already_running") from None
        if args.action == "key-init":
            if args.enable_wakeup or args.mode != "synthetic":
                raise DialogError("candidate_init_option_rejected")
            from .wakeup_key import provision
            print(json.dumps(provision(directory)))
            return 0
        key = None
        if args.enable_wakeup:
            from .wakeup_key import load
            key = load(directory)
        isolated = None
        if args.mode == "github":
            from .preflight import read_configured_token
            from .isolated_read import IsolatedRead
            path = _interactive_secret("Путь уже настроенного credential-файла (не передавать в мост): ")
            token = read_configured_token(path)
            del path
            isolated = IsolatedRead(token)
            del token
        host = WakeupHost(PanelApplication(authority, SnapshotReader(isolated=isolated), args.port,
                                          gateway=args.mode=='gateway'), key=key)
        del key
        print(json.dumps({"status": "starting", "source": args.mode, "port": args.port,
                          "wakeup_enabled": args.enable_wakeup, "installed_services": False}), flush=True)
        serve_candidate(host, args.port)
        return 0
    except KeyboardInterrupt:
        return 130
    except DialogError as exc:
        print(json.dumps({"error": exc.code}))
        return 1
    except Exception:
        print('{"error":"candidate_failed"}')
        return 1
    finally:
        if lock is not None: os.close(lock)
        if gateway_lock is not None:
            path,dev,ino = gateway_lock
            try:
                info = path.lstat()
                if (info.st_dev,info.st_ino)==(dev,ino): path.unlink()
            except FileNotFoundError: pass


if __name__ == "__main__":
    raise SystemExit(main())

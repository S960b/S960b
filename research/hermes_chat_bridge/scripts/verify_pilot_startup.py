"""Black-box startup admission checks, using synthetic DBs and loopback only."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
PYTHON = sys.executable
EXPECTED_REVISION = sys.argv[2] if len(sys.argv) > 2 else None
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def probe(process, port):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return 'exited', process.returncode
        try:
            with HTTP.open(f'http://127.0.0.1:{port}/ping', timeout=.3) as response:
                if response.status == 200:
                    return 'ready', json.load(response)
        except (OSError, urllib.error.URLError):
            time.sleep(.05)
    raise AssertionError('child neither rejected startup nor became healthy')


def main():
    passed = []
    children = []
    logs = []
    with tempfile.TemporaryDirectory(prefix='hermes-verify-pilot-', dir='/tmp') as home:
        directory = Path(home)
        db = directory / 'pilot.db'
        port = free_port()
        base = f'http://127.0.0.1:{port}'
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': home,
               'LANG': 'C.UTF-8', 'PYTHONUTF8': '1',
               'BRIDGE_HOST': '127.0.0.1', 'BRIDGE_PORT': str(port),
               'BRIDGE_BASE': base, 'BRIDGE_USER': 'owner',
               'BRIDGE_PASS': secrets.token_urlsafe(32)}

        def launch(settings):
            log = tempfile.TemporaryFile(mode='w+')
            logs.append(log)
            process = subprocess.Popen([PYTHON, str(ROOT / 'bridge_server.py')],
                cwd=ROOT, env=settings, stdout=log, stderr=subprocess.STDOUT)
            children.append(process)
            return process

        def rejected(settings, label):
            process = launch(settings)
            try:
                kind, info = probe(process, int(settings['BRIDGE_PORT']))
                assert kind == 'exited' and info != 0, f'{label}: unsafe startup accepted ({kind})'
                passed.append(label)
                print('PASS', label)
            finally:
                stop(process)

        try:
            rejected(dict(env), 'missing explicit DB path rejected')
            assert not (directory / 'hermes-chat-bridge' / 'data' / 'bridge.db').exists()
            legacy = directory / 'legacy.db'
            with sqlite3.connect(legacy) as connection:
                connection.execute('CREATE TABLE legacy_marker(value TEXT)')
                connection.execute("INSERT INTO legacy_marker VALUES('synthetic-old-db')")
            before = hashlib.sha256(legacy.read_bytes()).digest()
            rejected(dict(env, BRIDGE_DB_PATH=str(legacy)), 'unversioned legacy DB rejected')
            assert hashlib.sha256(legacy.read_bytes()).digest() == before, 'legacy bytes mutated'

            pilot_env = dict(env, BRIDGE_DB_PATH=str(db))
            first = launch(pilot_env)
            kind, health = probe(first, port)
            assert kind == 'ready', 'fresh pilot DB failed to start'
            assert health['version'] == '0.2.0'
            assert health['process_mode'] == 'single-process-pilot'
            if EXPECTED_REVISION:
                assert health['git_revision'] == EXPECTED_REVISION, health
            print('PROCESS_BUILD=', json.dumps(health, sort_keys=True))
            assert db.exists(), 'explicit pilot DB not created'
            with sqlite3.connect(db) as connection:
                assert connection.execute("SELECT name FROM sqlite_master WHERE name='subs'").fetchone()
            assert not (directory / 'hermes-chat-bridge' / 'data' / 'bridge.db').exists(), 'implicit DB was used'
            passed.append('fresh explicit pilot DB selected')
            print('PASS', passed[-1])

            # Different bind port but same issuer/DB: rejection cannot be a port collision.
            second_port = free_port()
            rejected(dict(pilot_env, BRIDGE_PORT=str(second_port)), 'second process on same DB rejected')
            stop(first)
            restart_port = free_port()
            restart = launch(dict(pilot_env, BRIDGE_PORT=str(restart_port)))
            kind, health = probe(restart, restart_port)
            assert kind == 'ready', 'verified pilot DB cannot reopen after clean stop'
            stop(restart)
            passed.append('pilot DB reopened after lock release')
            print('PASS', passed[-1])
            print('PILOT_STARTUP_CHECKS=', len(passed))
        finally:
            for process in children:
                stop(process)
            for log in logs:
                log.close()


if __name__ == '__main__':
    main()

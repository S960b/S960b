"""Bounded actual child-process acceptance, no HTTP/Git/token/owner data."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .isolated_read import _dispose


def run():
    command = [sys.executable, '-m', 'git_dialog.isolated_read']
    env = {'PYTHONPATH': str(Path(__file__).resolve().parent.parent), 'LANG': 'C.UTF-8'}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env, close_fds=True)
    try:
        # No input/EOF: the fixed child blocks reading stdin, before any network.
        try:
            process.wait(timeout=0.1)
            raise RuntimeError('child exited unexpectedly')
        except subprocess.TimeoutExpired:
            pass
    finally:
        _dispose(process)
    if process.poll() is None: raise RuntimeError('child remains')
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env, close_fds=True)
    try:
        output, _ = process.communicate(b'{}', timeout=2)
        if process.returncode != 1 or json.loads(output) != {
                'snapshot': None, 'error': 'poll_child_source_error'}:
            raise RuntimeError('child protocol')
    finally:
        _dispose(process)
    return {'status': 'passed', 'blocked_child_reaped': True, 'invalid_input_rejected': True,
            'git_network_credentials_used': False, 'old_services_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', required=True, action='store_true')
    parser.parse_args(argv)
    try: result = run()
    except Exception: result = {'error': 'isolated_read_smoke_failed'}
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())

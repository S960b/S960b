"""Explicit pinned beta bootstrap. Default check does not import/start bridge."""
import argparse
import ast
import functools
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

from . import DialogError
from .bridge_patch import EXPECTED as CONTEXT_SHA, LIMIT

CANDIDATE_SHA = '30e6307ed09313fcf21fa947380214e5adbb9d2b35442ed88d728c95edbbd11f'
PHASES = frozenset({'options', 'context_read', 'candidate_read', 'state_validation',
    'key_validation', 'module_execute', 'configure', 'build_app', 'enable_wakeup',
    'enable_cleanup', 'main', 'result'})
ERROR_TYPES = frozenset({'DialogError', 'FileNotFoundError', 'PermissionError',
    'BlockingIOError', 'TimeoutError', 'ModuleNotFoundError', 'ImportError',
    'AttributeError', 'NameError', 'TypeError', 'ValueError', 'OSError',
    'RuntimeError', 'SyntaxError', 'UnicodeError', 'OperationalError'})
ERROR_CODES = frozenset({'bootstrap_file_rejected', 'bootstrap_source_changed',
    'bootstrap_port_rejected', 'bootstrap_context_rejected',
    'bootstrap_check_option_rejected', 'bootstrap_state_required',
    'bootstrap_already_loaded', 'service_state_directory_rejected',
    'service_state_file_rejected', 'wrong_service_database', 'invalid_service_identity',
    'wakeup_key_file_rejected', 'wakeup_key_directory_rejected', 'wakeup_key_io_failed'})


class BoundaryFailure(RuntimeError):
    """Keep only fixed diagnostic tags, never exception messages or paths."""
    def __init__(self, phase, error):
        super().__init__('bootstrap_failed')
        self.phase = phase if type(phase) is str and phase in PHASES else 'options'
        kind = type(error).__name__
        self.kind = kind if kind in ERROR_TYPES else 'Exception'
        code = error.code if isinstance(error, DialogError) else None
        self.code = code if type(code) is str and code in ERROR_CODES else 'unclassified'


def _failure(phase, error):
    safe = error if isinstance(error, BoundaryFailure) else BoundaryFailure(phase, error)
    return {'error': 'bootstrap_failed', 'phase': safe.phase,
            'type': safe.kind, 'code': safe.code}


def _read(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > LIMIT):
            raise DialogError('bootstrap_file_rejected')
        raw = os.read(fd, LIMIT + 1)
    finally: os.close(fd)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise DialogError('bootstrap_source_changed')
    ast.parse(raw)  # Syntax only; no module globals evaluated in check mode.
    return raw


def _configure(module, state_dir, port):
    original = module.build_app
    @functools.wraps(original)
    def build(*args, **kwargs):
        try:
            app = original(*args, **kwargs)
        except Exception as error:
            raise BoundaryFailure('build_app', error) from None
        try:
            app.state.bridge.enable_beta_wakeup(state_dir, port=port)
        except Exception as error:
            try:
                app.state.pilot_lock.close()
            except Exception as cleanup:
                raise BoundaryFailure('enable_cleanup', cleanup) from None
            raise BoundaryFailure('enable_wakeup', error) from None
        except BaseException:
            app.state.pilot_lock.close()
            raise
        return app
    module.build_app = build
    return original


def _execute(raw, context, state_dir, port):
    # This is explicitly selected, hash-pinned local application CODE, never
    # a Git/job body, arbitrary operation or callback selected by message data.
    name = '_git_dialog_pinned_beta_bridge'
    if name in sys.modules: raise DialogError('bootstrap_already_loaded')
    module = types.ModuleType(name)
    module.__file__ = str(context)
    module.__package__ = ''
    previous_path, previous_argv = list(sys.path), list(sys.argv)
    sys.modules[name] = module
    phase = 'module_execute'
    try:
        # Keep the original script's sibling imports and __file__-based paths,
        # rather than treating the candidate temporary directory as live state.
        sys.path.insert(0, str(context.parent))
        sys.argv = [str(context)]
        exec(compile(raw, str(context), 'exec'), module.__dict__)
        phase = 'configure'
        _configure(module, state_dir, port)
        phase = 'main'
        return module.main()
    except BoundaryFailure:
        raise
    except Exception as error:
        raise BoundaryFailure(phase, error) from None
    finally:
        sys.path[:] = previous_path
        sys.argv[:] = previous_argv
        sys.modules.pop(name, None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Pinned generated candidate, not a credential')
    parser.add_argument('--context-source', required=True, help='Unchanged original bridge source')
    parser.add_argument('--state-dir', help='Separate candidate state; run or explicit validation only')
    parser.add_argument('--port', type=int, default=8790)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--validate-state', action='store_true',
                       help='Validate existing owner DB/key only, never execute/import bridge')
    args = parser.parse_args(argv)
    phase = 'options'
    try:
        if type(args.port) is not int or not 1024 < args.port <= 65535 or args.port in (8787, 8789):
            raise DialogError('bootstrap_port_rejected')
        context = Path(args.context_source).absolute()
        if context.name != 'bridge_server.py': raise DialogError('bootstrap_context_rejected')
        phase = 'context_read'
        _read(context, CONTEXT_SHA)
        phase = 'candidate_read'
        raw = _read(args.source, CANDIDATE_SHA)
        if not args.run and not args.validate_state:
            phase = 'options'
            if args.state_dir is not None: raise DialogError('bootstrap_check_option_rejected')
            print(json.dumps({'status': 'source_checked', 'candidate_sha256': CANDIDATE_SHA,
                              'context_sha256': CONTEXT_SHA, 'bridge_started': False}))
            return 0
        phase = 'options'
        if args.state_dir is None: raise DialogError('bootstrap_state_required')
        phase = 'state_validation'
        from .service import AuthAuthority
        AuthAuthority(args.state_dir)
        phase = 'key_validation'
        from .wakeup_key import load
        key = load(args.state_dir)  # Validate prerequisites before old bridge code runs.
        del key
        if args.validate_state:
            print(json.dumps({'status': 'prerequisites_checked', 'state_validated': True,
                              'key_validated': True, 'bridge_started': False}))
            return 0
        # Existing bridge runtime/interpreter/environment/CWD must be retained.
        # No service stop, port takeover, systemd edit or credential extraction.
        result = _execute(raw, context, args.state_dir, args.port)
        phase = 'result'
        return 0 if result is None else int(result)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(json.dumps(_failure(phase, error)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

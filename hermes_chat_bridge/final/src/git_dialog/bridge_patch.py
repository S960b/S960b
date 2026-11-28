"""Hash-pinned candidate-only bridge wiring; never imports/executes the source."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat

from . import DialogError

EXPECTED = 'ffbbe5b4252f16544ed9de09f4a715cff2cb4347199866c47ba4584eb726647f'
LIMIT = 512 * 1024
NATIVE_STATE = '/home/kali/agent-dialog-beta/state'
NATIVE_SRC = '/home/kali/agent-dialog-beta/code/src'


def _one(items):
    items = list(items)
    if len(items) != 1: raise DialogError('bridge_patch_shape_changed')
    return items[0]


def _rewrite(source, *, native=False):
    """Pure transformation, preserving every original byte except one close line."""
    tree = ast.parse(source)
    cls = _one(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BridgeApp')
    if any(isinstance(n, ast.FunctionDef) and n.name == 'enable_beta_wakeup' for n in cls.body):
        raise DialogError('bridge_patch_already_present')
    init = _one(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    emit = _one(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'emit_test_event')
    payload = _one(n for n in ast.walk(emit) if isinstance(n, ast.Assign) and
                   any(isinstance(t, ast.Name) and t.id == 'payload' for t in n.targets))
    build = _one(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_app')
    life = _one(n for n in ast.walk(build) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'lifespan')
    context = _one(n for n in ast.walk(life) if isinstance(n, ast.AsyncWith) and
                   any(isinstance(i.context_expr, ast.Call) and
                       isinstance(i.context_expr.func, ast.Name) and
                       i.context_expr.func.id == 'original_lifespan' for i in n.items))
    close = _one(n for n in ast.walk(life) if isinstance(n, ast.Expr) and
                 isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and
                 isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'pilot_lock' and
                 n.value.func.attr == 'close')
    lines = source.splitlines(keepends=True)
    if not source.endswith('\n') or '\r' in source: raise DialogError('bridge_patch_line_format')
    def indent(node): return ' ' * node.col_offset
    edits = []
    p = ' ' * (init.col_offset + 4)
    method = ('\n' + ' ' * init.col_offset + 'def enable_beta_wakeup(self, state_dir, *, port=8790):\n' +
        p + 'if self._beta_forwarder is not None:\n' + p + '    raise RuntimeError("beta_already_configured")\n' +
        p + 'from git_dialog.wakeup_key import load\n' +
        p + 'from git_dialog.wakeup_forward import WakeupForwarder\n' +
        p + 'self._beta_forwarder = WakeupForwarder(load(state_dir), port=port)\n')
    if native:
        # Preserve the native entrypoint and its existing credential preparation.
        # Default-off activation does not import this package or read its key.
        method = ('\n' + ' ' * init.col_offset +
            'def enable_beta_wakeup(self, state_dir, *, port=8790):\n' +
            p + 'if self._beta_forwarder is not None:\n' +
            p + '    raise RuntimeError("beta_already_configured")\n' +
            p + 'import sys\n' + p + 'previous_path = list(sys.path)\n' +
            p + 'try:\n' + p + '    sys.path.insert(0, ' + repr(NATIVE_SRC) + ')\n' +
            p + '    from git_dialog.wakeup_key import load\n' +
            p + '    from git_dialog.wakeup_forward import WakeupForwarder\n' +
            p + '    self._beta_forwarder = WakeupForwarder(load(state_dir), port=port)\n' +
            p + 'finally:\n' + p + '    sys.path[:] = previous_path\n' +
            '\n' + ' ' * init.col_offset + 'def configure_beta_wakeup(self):\n' +
            p + 'import os, stat\n' + p + 'try:\n' +
            p + '    fd = os.open(' + repr(NATIVE_STATE + '/bridge-wakeup.enabled') +
            ', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n' +
            p + 'except FileNotFoundError:\n' + p + '    return\n' +
            p + 'try:\n' + p + '    info = os.fstat(fd)\n' +
            p + '    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or\n' +
            p + '            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0):\n' +
            p + '        raise RuntimeError("beta_activation_rejected")\n' +
            p + 'finally:\n' + p + '    os.close(fd)\n' +
            p + 'self.enable_beta_wakeup(' + repr(NATIVE_STATE) + ', port=8790)\n')
    edits.append((init.end_lineno, init.end_lineno, p + 'self._beta_forwarder = None\n' + method))
    p = indent(payload)
    edits.append((payload.end_lineno, payload.end_lineno,
                  p + 'if self._beta_forwarder is not None:\n' + p + '    self._beta_forwarder.offer(payload)\n'))
    p = indent(context)
    edits.append((context.lineno - 1, context.lineno - 1,
                  p + 'if bridge._beta_forwarder is not None:\n' + p + '    bridge._beta_forwarder.start()\n'))
    p = indent(close)
    original = ''.join(lines[close.lineno - 1:close.end_lineno])
    if original.strip() != 'pilot_lock.close()': raise DialogError('bridge_patch_close_changed')
    edits.append((close.lineno - 1, close.end_lineno, p + 'try:\n' +
        p + '    if bridge._beta_forwarder is not None:\n' + p + '        bridge._beta_forwarder.stop()\n' +
        p + 'finally:\n' + p + '    pilot_lock.close()\n'))
    if native:
        assignment = _one(n for n in build.body if isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Attribute) and t.attr == 'bridge' and
                isinstance(t.value, ast.Attribute) and t.value.attr == 'state' and
                isinstance(t.value.value, ast.Name) and t.value.value.id == 'app'
                for t in n.targets) and isinstance(n.value, ast.Name) and n.value.id == 'bridge')
        original_context = _one(n for n in build.body if isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Name) and t.id == 'original_lifespan' for t in n.targets))
        lock_binding = _one(n for n in build.body if isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Name) and t.id == 'pilot_lock' for t in n.targets))
        if (assignment.end_lineno >= original_context.lineno or
                lock_binding.end_lineno >= original_context.lineno or
                original_context.end_lineno >= life.lineno):
            raise DialogError('bridge_patch_shape_changed')
        p = indent(original_context)
        edits.append((original_context.end_lineno, original_context.end_lineno,
            p + 'try:\n' + p + '    bridge.configure_beta_wakeup()\n' +
            p + 'except BaseException:\n' + p + '    pilot_lock.close()\n' + p + '    raise\n'))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    candidate = ''.join(lines)
    ast.parse(candidate)  # Syntax validation only, no imports/side effects.
    return candidate


def candidate(source, *, native=False):
    if len(source) > LIMIT or hashlib.sha256(source).hexdigest() != EXPECTED:
        raise DialogError('bridge_patch_source_changed')
    try: return _rewrite(source.decode('utf-8'), native=native).encode('utf-8')
    except (SyntaxError, UnicodeError): raise DialogError('bridge_patch_invalid_source') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--native-entry', action='store_true',
                        help='Candidate only; preserve original startup and default-off local opt-in')
    args = parser.parse_args(argv)
    fd = out = None
    try:
        fd = os.open(args.source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise DialogError('bridge_patch_file_rejected')
        if args.native_entry and (info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) & 0o022 or info.st_nlink != 1):
            raise DialogError('bridge_patch_file_rejected')
        raw = os.read(fd, LIMIT + 1)
        content = candidate(raw, native=args.native_entry)
        # New file only; never overwrite source, existing candidate or backup.
        out = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(out, 'wb') as stream:
            out = None
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        result = {'status': 'candidate_created', 'source_sha256': EXPECTED,
            'candidate_sha256': hashlib.sha256(content).hexdigest(), 'live_source_modified': False}
        if args.native_entry:
            result.update(native_entry_preserved=True, wakeup_default_enabled=False)
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"error":"bridge_patch_failed_no_live_write"}')
        return 1
    finally:
        if fd is not None: os.close(fd)
        if out is not None: os.close(out)


if __name__ == '__main__':
    raise SystemExit(main())

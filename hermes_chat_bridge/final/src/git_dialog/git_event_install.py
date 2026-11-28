"""P26 exact-source opt-in binding to the native public delivery API.

Extract ONLY a direct self.<attribute> receiver shared by the two public store
methods in the pinned emit_test_event source. No guessed service attribute,
runtime inspection/import, helper-following, unit/credential/service action.
"""
import argparse
import ast
import hashlib
import json
import os

from . import DialogError
from .gateway_patch import SOURCE, BASE, _new
from .native_install import _directory, _read, _absent, _sync

BEFORE = 'cf7db37a355d8a2acbf0d97fdbd9a440730b14ab4ad66cee32fc40fc3ea96b3d'
BACKUP = BASE / 'bridge-before-git-events.p26.py'
STAGE = SOURCE.parent / '.bridge-git-events.p26-stage'
ARCHIVE = BASE / 'bridge-git-events.p26-rollback.py'
RESTORE = SOURCE.parent / '.bridge-git-events.p26-restore'


class ShapeError(DialogError):
    """Static guard name only; no source, expressions or runtime values."""
    def __init__(self, guard):
        super().__init__('git_event_native_shape_changed')
        self.guard = guard


def one(values, guard):
    values = list(values)
    if len(values) != 1: raise ShapeError(guard)
    return values[0]


def rewrite(source):
    if not source.endswith('\n') or '\r' in source:
        raise ShapeError('line_ending')
    tree = ast.parse(source)
    bridge = one((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BridgeApp'), 'bridge_class')
    emit = one((n for n in bridge.body if isinstance(n, ast.FunctionDef) and n.name == 'emit_test_event'), 'emit_method')
    receivers = []
    for method in ('active_subscriptions', 'enqueue_delivery'):
        call = one((n for n in ast.walk(emit) if isinstance(n, ast.Call) and
            isinstance(n.func, ast.Attribute) and n.func.attr == method), method + '_call')
        receiver = call.func.value
        if (not isinstance(receiver, ast.Attribute) or not isinstance(receiver.value, ast.Name) or
                receiver.value.id != 'self' or receiver.attr.startswith('_')):
            raise ShapeError('store_receiver')
        receivers.append(receiver.attr)
    if receivers[0] != receivers[1]: raise ShapeError('store_receiver')
    # Enforce the actual public method contract, without reading runtime data.
    # P26b exact guarded native-source report: the class is Store, NOT the
    # earlier report's informal "BridgeStore" label. The pre-install lifespan
    # call intentionally has no notification_store keyword yet.
    store = one((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Store'), 'store_class')
    for name, args in (('active_subscriptions', ['self']),
                       ('enqueue_delivery', ['self', 'sub_id', 'event_id', 'payload'])):
        fn = one((n for n in store.body if isinstance(n, ast.FunctionDef) and n.name == name), name + '_method')
        if ([a.arg for a in fn.args.args] != args or fn.args.vararg or fn.args.kwarg or
                fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.defaults):
            raise ShapeError(name + '_signature')
    build = one((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_app'), 'build_function')
    call = one((n for n in ast.walk(build) if isinstance(n, ast.Call) and
        isinstance(n.func, ast.Name) and n.func.id == 'install_gateway_lifespan'), 'lifespan_call')
    if ([ast.unparse(a) for a in call.args] != ['app', 'bridge'] or
            [(k.arg, ast.unparse(k.value)) for k in call.keywords] !=
            [('dialog_factory', 'dialog_factory_from_systemd')] or call.lineno != call.end_lineno):
        raise ShapeError('lifespan_arguments')
    lines = source.splitlines(keepends=True)
    line = lines[call.lineno - 1]
    # col_offset/end_col_offset are UTF-8 byte offsets. Preserve every other
    # byte, including pre-existing dirty source and the original script entry.
    raw = line.encode('utf-8')
    offset = call.end_col_offset - 1
    if raw[offset:offset+1] != b')': raise ShapeError('call_line')
    lines[call.lineno - 1] = (raw[:offset] +
        (', notification_store=bridge.' + receivers[0]).encode('ascii') + raw[offset:]).decode('utf-8')
    result = ''.join(lines); ast.parse(result)
    return result


def candidate(raw):
    if hashlib.sha256(raw).hexdigest() != BEFORE:
        raise DialogError('git_event_native_source_changed')
    return rewrite(raw.decode('utf-8')).encode('utf-8')


def run(action):
    phase, completed = 'guards', []
    try:
        _directory(BASE, 0o700); _directory(SOURCE.parent)
        if action == 'rollback':
            before, _ = _read(BACKUP, BEFORE, 0o600); after = candidate(before)
            current, identity = _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)
            _absent(ARCHIVE, RESTORE)
            phase = 'archive'; _new(ARCHIVE, current, 0o600)
            phase = 'restore_stage'; _new(RESTORE, before, 0o644)
            phase = 'source_recheck'
            if _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)[1] != identity:
                raise DialogError('git_event_native_source_changed')
            phase = 'replace'; os.replace(RESTORE, SOURCE); completed.append('bridge')
            phase = 'directory_sync'; _sync(SOURCE.parent); _read(SOURCE, BEFORE, 0o644)
            status = 'git_event_native_restored'
        elif action in ('check', 'install'):
            before, identity = _read(SOURCE, BEFORE, 0o644)
            phase = 'shape'; after = candidate(before)
            _absent(BACKUP, STAGE, ARCHIVE, RESTORE)
            status = 'git_event_native_checked'
            if action == 'install':
                phase = 'backup'; _new(BACKUP, before, 0o600)
                phase = 'stage'; _new(STAGE, after, 0o644)
                phase = 'source_recheck'
                if _read(SOURCE, BEFORE, 0o644)[1] != identity:
                    raise DialogError('git_event_native_source_changed')
                phase = 'replace'; os.replace(STAGE, SOURCE); completed.append('bridge')
                phase = 'directory_sync'; _sync(SOURCE.parent)
                _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)
                status = 'git_event_native_installed'
        else: raise DialogError('git_event_install_action_rejected')
        return {'status': status, 'after_sha256': hashlib.sha256(after).hexdigest(),
                'completed_replacements': completed, 'service_action': False}
    except Exception as exc:
        result = {'error': 'git_event_install_failed', 'phase': phase, 'completed_replacements': completed,
                  'service_action': False}
        if type(exc) is ShapeError: result['guard'] = exc.guard
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install', 'rollback'))
    result = run(parser.parse_args(argv).action)
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__': raise SystemExit(main())

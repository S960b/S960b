"""P22 pinned native lifespan insertion; explicit backups and strict rollback.

BEFORE is the actually installed P21 registration bridge, not stale2714. No
service/unit/credential/marker actions. Errors retain partial artifacts and
record actual renames even if a later fsync fails. No retry/automatic undo.
"""
import argparse
import ast
import hashlib
import json
import os

from . import DialogError
from .gateway_patch import SOURCE, BASE, _new
from .native_install import _directory, _read, _absent, _sync

BEFORE = '066f9fbbb38b8009112456ea61beab76f9ab4a740eeb8a9d8cc62b5bfb5b43df'
BACKUP = BASE / 'bridge-before-gateway-runtime.p22.py'
STAGE = SOURCE.parent / '.bridge-gateway-runtime.p22-stage'
ARCHIVE = BASE / 'bridge-gateway-runtime.p22-rollback.py'
RESTORE = SOURCE.parent / '.bridge-gateway-runtime.p22-restore'


def rewrite(source):
    if not source.endswith('\n') or '\r' in source or 'git_dialog.gateway_lifespan' in source:
        raise DialogError('gateway_runtime_shape_changed')
    tree = ast.parse(source)
    builds = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_app']
    if len(builds) != 1:
        raise DialogError('gateway_runtime_shape_changed')
    nodes = [n for n in builds[0].body if isinstance(n, ast.Assign) and
        len(n.targets) == 1 and ast.dump(n.targets[0]) == ast.dump(ast.Attribute(
            value=ast.Attribute(value=ast.Name(id='app', ctx=ast.Load()), attr='router', ctx=ast.Load()),
            attr='lifespan_context', ctx=ast.Store())) and
        isinstance(n.value, ast.Name) and n.value.id == 'lifespan']
    if len(nodes) != 1 or 'git_dialog.native_gateway' not in source:
        raise DialogError('gateway_runtime_shape_changed')
    if any(isinstance(n, ast.Name) and n.id in ('install_gateway_lifespan', 'dialog_factory_from_systemd')
           for n in ast.walk(tree)):
        raise DialogError('gateway_runtime_shape_changed')
    node = nodes[0]; p = ' ' * node.col_offset
    addition = (p + 'from git_dialog.gateway_lifespan import install_lifespan as install_gateway_lifespan\n' +
        p + 'from git_dialog.gateway_credentials import dialog_factory_from_systemd\n' +
        p + 'install_gateway_lifespan(app, bridge, dialog_factory=dialog_factory_from_systemd)\n')
    lines = source.splitlines(keepends=True)
    lines[node.end_lineno:node.end_lineno] = [addition]
    result = ''.join(lines); ast.parse(result)
    return result


def candidate(raw):
    if hashlib.sha256(raw).hexdigest() != BEFORE:
        raise DialogError('gateway_runtime_source_changed')
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
                raise DialogError('gateway_runtime_source_changed')
            phase = 'replace'; os.replace(RESTORE, SOURCE); completed.append('bridge')
            phase = 'directory_sync'; _sync(SOURCE.parent); _read(SOURCE, BEFORE, 0o644)
            status = 'gateway_runtime_restored'
        elif action in ('check','install'):
            before, identity = _read(SOURCE, BEFORE, 0o644)
            phase = 'shape'; after = candidate(before)
            _absent(BACKUP, STAGE, ARCHIVE, RESTORE)
            status = 'gateway_runtime_checked'
            if action == 'install':
                phase = 'backup'; _new(BACKUP, before, 0o600)
                phase = 'stage'; _new(STAGE, after, 0o644)
                phase = 'source_recheck'
                if _read(SOURCE, BEFORE, 0o644)[1] != identity:
                    raise DialogError('gateway_runtime_source_changed')
                phase = 'replace'; os.replace(STAGE, SOURCE); completed.append('bridge')
                phase = 'directory_sync'; _sync(SOURCE.parent)
                _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)
                status = 'gateway_runtime_installed'
        else:
            raise DialogError('gateway_runtime_action_rejected')
        return {'status':status,'source_before_sha256':BEFORE,
            'candidate_sha256':hashlib.sha256(after).hexdigest(), 'completed_replacements':completed,
            'service_restarted':False,'activation_modified':False}
    except Exception:
        return {'error':'gateway_runtime_patch_failed','action':action,'phase':phase,
                'completed_replacements':completed,'retry_authorized':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check','install','rollback'))
    result = run(parser.parse_args(argv).action)
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())

"""One hash-pinned MCP registration insertion and explicit single-file installer.

No source import/execute, credentials, service actions, CLI replacement or
activation changes. Installation computes the exact insertion from the pinned
BEFORE bytes; rollback accepts only that deterministic AFTER and new backup.
All artifacts are NEW. On any failure retain partial files and STOP, no retry.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path

from . import DialogError
from .native_install import _directory, _read, _absent, _sync

SOURCE = Path('/home/kali/S960b/research/hermes_chat_bridge/bridge_server.py')
BASE = Path('/home/kali/agent-dialog-beta')
BEFORE = '2714aca2a7420cd2ff073baee498302f63ade28e49051ca546c69b62ddbb9ce5'
BACKUP = BASE / 'bridge-before-gateway.p21.py'
STAGE = SOURCE.parent / '.bridge-gateway.p21-stage'
ARCHIVE = BASE / 'bridge-gateway.p21-rollback.py'
RESTORE = SOURCE.parent / '.bridge-gateway.p21-restore'
PACKAGE = '/home/kali/agent-dialog-beta/code/src'


def rewrite(source):
    if not source.endswith('\n') or '\r' in source or 'git_dialog.native_gateway' in source:
        raise DialogError('gateway_patch_shape_changed')
    tree = ast.parse(source)
    builds = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_app']
    if len(builds) != 1:
        raise DialogError('gateway_patch_shape_changed')
    nodes = [n for n in builds[0].body if isinstance(n, ast.Assign) and
        len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'mcp' and
        isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and
        n.value.func.id == 'make_mcp_server' and not n.value.keywords and
        [ast.dump(x) for x in n.value.args] == [ast.dump(ast.Name(id=x, ctx=ast.Load()))
                                             for x in ('base_url', 'bridge')]]
    if len(nodes) != 1 or any(isinstance(n, ast.Name) and n.id == 'gateway_previous_path' for n in ast.walk(tree)):
        raise DialogError('gateway_patch_shape_changed')
    node = nodes[0]
    p = ' ' * node.col_offset
    addition = (p + 'import sys\n' + p + 'gateway_previous_path = list(sys.path)\n' +
        p + 'try:\n' + p + '    sys.path.insert(0, ' + repr(PACKAGE) + ')\n' +
        p + '    from git_dialog.native_gateway import register_mcp\n' +
        p + '    register_mcp(mcp, bridge)\n' + p + 'finally:\n' +
        p + '    sys.path[:] = gateway_previous_path\n')
    lines = source.splitlines(keepends=True)
    lines[node.end_lineno:node.end_lineno] = [addition]
    result = ''.join(lines)
    ast.parse(result)
    return result


def candidate(raw):
    if hashlib.sha256(raw).hexdigest() != BEFORE:
        raise DialogError('gateway_patch_source_changed')
    return rewrite(raw.decode('utf-8')).encode('utf-8')


def _new(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)  # ONLY the new O_EXCL inode.
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    _read(path, hashlib.sha256(raw).hexdigest(), mode)
    _sync(path.parent)


def run(action, output=None):
    phase, replaced = 'guards', False
    try:
        _directory(BASE, 0o700); _directory(SOURCE.parent)
        if action not in ('check', 'candidate', 'install', 'rollback'):
            raise DialogError('gateway_patch_action_rejected')
        if action == 'rollback':
            before, _ = _read(BACKUP, BEFORE, 0o600)
            after = candidate(before)
            current, identity = _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)
            _absent(ARCHIVE, RESTORE)
            phase = 'archive'; _new(ARCHIVE, current, 0o600)
            phase = 'restore_stage'; _new(RESTORE, before, 0o644)
            phase = 'source_recheck'
            if _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)[1] != identity:
                raise DialogError('gateway_patch_source_changed')
            phase = 'replace'; os.replace(RESTORE, SOURCE); replaced = True
            phase = 'directory_sync'; _sync(SOURCE.parent)
            _read(SOURCE, BEFORE, 0o644)
            status = 'gateway_bridge_restored'
        else:
            before, identity = _read(SOURCE, BEFORE, 0o644)
            phase = 'shape'; after = candidate(before)
            if action == 'candidate':
                if output is None:
                    raise DialogError('gateway_candidate_output_required')
                _directory(Path(output).parent); _absent(Path(output))
                phase = 'candidate_write'; _new(Path(output), after, 0o600)
                status = 'gateway_candidate_created'
            else:
                _absent(BACKUP, STAGE, ARCHIVE, RESTORE)
                status = 'gateway_install_checked'
                if action == 'install':
                    phase = 'backup'; _new(BACKUP, before, 0o600)
                    phase = 'stage'; _new(STAGE, after, 0o644)
                    phase = 'source_recheck'
                    if _read(SOURCE, BEFORE, 0o644)[1] != identity:
                        raise DialogError('gateway_patch_source_changed')
                    phase = 'replace'; os.replace(STAGE, SOURCE); replaced = True
                    phase = 'directory_sync'; _sync(SOURCE.parent)
                    _read(SOURCE, hashlib.sha256(after).hexdigest(), 0o644)
                    status = 'gateway_bridge_installed'
        return {'status': status, 'source_before_sha256': BEFORE,
            'candidate_sha256': hashlib.sha256(after).hexdigest(),
            'completed_replacements': ['bridge'] if replaced else [],
            'service_restarted': False, 'activation_modified': False}
    except Exception:
        return {'error': 'gateway_patch_failed', 'action': action, 'phase': phase,
                'completed_replacements': ['bridge'] if replaced else []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'candidate', 'install', 'rollback'))
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if args.output is not None and args.action != 'candidate':
        result = {'error': 'gateway_patch_failed', 'action': args.action, 'phase': 'options',
                  'completed_replacements': []}
    else:
        result = run(args.action, args.output)
    print(json.dumps(result, sort_keys=True))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())

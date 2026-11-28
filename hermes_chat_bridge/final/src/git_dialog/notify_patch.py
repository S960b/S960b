"""Two hash-pinned candidate-only notification hooks; no import/execute/install."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat

from .preflight import TASK_ID

DIRECTORY = Path('/home/kali/S960b/research/hermes_chat_bridge')
FILES = {'bridge': DIRECTORY / 'bridge_server.py', 'cli': DIRECTORY / 'bridge_cli.py'}
HASHES = {'bridge': '4921809e5aa9e7a67f7bee77f355d58123b076507d9d8f54da6b67dd4451c585',
          'cli': '0d2cdcb6e034b05a3526c55d7dd0efa2cead886b2fdc82b6f9d3165272e63d67'}
PACKAGE = '/home/kali/agent-dialog-beta/code/src'
LIMIT = 512 * 1024


PHASES = {'directory', 'source_read', 'source_metadata', 'source_stability',
          'source_hash', 'shape', 'cli_put_anchor', 'cli_parent_block',
          'cli_bridge_anchor', 'cli_pending_guard', 'cli_order', 'output_write'}


class ShapeError(ValueError):
    def __init__(self, phase):
        self.phase = phase if phase in PHASES else 'shape'
        super().__init__('shape_changed')


def _one(items, phase='shape'):
    values = list(items)
    if len(values) != 1:
        raise ShapeError(phase)
    return values[0]


def rewrite(source, kind):
    if not source.endswith('\n') or '\r' in source or 'native_notify' in source:
        raise ValueError('shape_changed')
    tree = ast.parse(source)
    if kind == 'bridge':
        node = _one(n for n in ast.walk(tree) if isinstance(n, ast.Expr) and
                    isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and
                    isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'bridge' and
                    n.value.func.attr == 'configure_beta_wakeup' and not n.value.args and not n.value.keywords)
        build = _one(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_app')
        if node not in list(ast.walk(build)):
            raise ValueError('shape_changed')
        indent = ' ' * node.col_offset
        text = (indent + 'if bridge._beta_forwarder is not None:\n' +
                indent + '    import sys\n' + indent + '    notify_previous_path = list(sys.path)\n' +
                indent + '    try:\n' + indent + '        sys.path.insert(0, ' + repr(PACKAGE) + ')\n' +
                indent + '        from git_dialog.native_notify import attach\n' +
                indent + '        attach(app, bridge)\n' + indent + '    finally:\n' +
                indent + '        sys.path[:] = notify_previous_path\n')
        line = node.end_lineno
    elif kind == 'cli':
        put = _one((n for n in ast.walk(tree) if isinstance(n, ast.Assign) and
                   len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'r' and
                   isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and
                   isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'q' and
                   n.value.func.attr == 'put_message'), 'cli_put_anchor')
        # The CLI may construct unrelated BridgeApp instances in other branches.
        # Bind the hook to the unique direct statement list of the report put.
        body = _one((v for parent in ast.walk(tree) for _, v in ast.iter_fields(parent)
                    if isinstance(v, list) and put in v), 'cli_parent_block')
        node = _one((n for n in body if isinstance(n, ast.Assign) and
                    len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'bridge' and
                    isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and
                    isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'bs' and
                    n.value.func.attr == 'BridgeApp'), 'cli_bridge_anchor')
        guard_shape = ast.parse("r['status'] != 'pending'", mode='eval').body
        guards = [n for n in body if isinstance(n, ast.If) and
                  ast.dump(n.test) == ast.dump(guard_shape) and
                  any(isinstance(x, ast.Return) for x in n.body)]
        guard = _one(guards, 'cli_pending_guard')
        if not put.end_lineno < guard.lineno <= guard.end_lineno < node.lineno:
            raise ShapeError('cli_order')
        indent = ' ' * node.col_offset
        text = (indent + "if queue == 'test' and task_id == " + repr(TASK_ID) + ':\n' +
                indent + '    notify_previous_path = list(sys.path)\n' + indent + '    try:\n' +
                indent + '        sys.path.insert(0, ' + repr(PACKAGE) + ')\n' +
                indent + '        from git_dialog.native_notify import notify\n' +
                indent + "        notify(r['job_id'])\n" + indent + '    except Exception:\n' +
                indent + '        pass  # Preserve original publication on notification failure.\n' +
                indent + '    finally:\n' + indent + '        sys.path[:] = notify_previous_path\n')
        line = node.lineno - 1
    else:
        raise ValueError('kind_rejected')
    lines = source.splitlines(keepends=True)
    lines[line:line] = [text]
    result = ''.join(lines)
    ast.parse(result)
    return result


def candidate(raw, kind):
    if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != HASHES[kind]:
        raise ValueError('source_changed')
    return rewrite(raw.decode('utf-8'), kind).encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['bridge', 'cli'], required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    phase = 'directory'
    try:
        from .native_install import _directory
        _directory(FILES[args.kind].parent)
        phase = 'source_read'
        fd = os.open(FILES[args.kind], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            phase = 'source_metadata'
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o644 or info.st_nlink != 1 or info.st_size > LIMIT):
                raise ValueError('file_rejected')
            phase = 'source_stability'
            raw = os.read(fd, LIMIT + 1)
            after = os.fstat(fd)
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('source_changed')
        finally:
            os.close(fd)
        phase = 'source_hash'
        if hashlib.sha256(raw).hexdigest() != HASHES[args.kind]:
            raise ValueError('source_changed')
        phase = 'shape'
        content = candidate(raw, args.kind)
        phase = 'output_write'
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({'status': 'notify_candidate_created', 'kind': args.kind,
                          'source_sha256': HASHES[args.kind],
                          'candidate_sha256': hashlib.sha256(content).hexdigest(),
                          'live_source_modified': False}))
        return 0
    except Exception as error:
        safe_phase = error.phase if isinstance(error, ShapeError) else phase
        print(json.dumps({'error': 'notify_candidate_failed_no_live_write',
                          'phase': safe_phase if safe_phase in PHASES else 'shape'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

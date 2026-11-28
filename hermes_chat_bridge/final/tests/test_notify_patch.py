import ast
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from git_dialog import bridge_patch, native_notify, notify_patch as patch
from git_dialog.preflight import TASK_ID
from test_native_bridge_patch import NATIVE

BRIDGE = bridge_patch._rewrite(NATIVE, native=True)
CLI = '''import sys
def main():
    queue = selected_queue
    task_id = selected_task
    r = q.put_message('fixture', queue=queue, label='report', task_id=task_id,
                      title=None, idempotency_key='fixed-fixture')
    if r['status'] != 'pending': print(r); return 1
    # PRESERVE_LOCAL_CHANGE
    bridge = bs.BridgeApp('http://127.0.0.1:8765')
    n = bridge.emit_test_event(r['job_id'])
    return 0
'''


def test_bridge_attach_after_configure_retains_all_original_bytes():
    changed = patch.rewrite(BRIDGE, 'bridge')
    ast.parse(changed)
    assert changed.index('attach(app, bridge)') > changed.index('bridge.configure_beta_wakeup()')
    assert changed.index('attach(app, bridge)') < changed.index('async def lifespan')
    assert "BRIDGE_PASS is required" in changed
    assert 'PRESERVE_EXISTING_CHANGE' in changed
    assert changed.endswith('if __name__ == "__main__":\n    main()\n')
    original_lines = iter(changed.splitlines())
    assert all(any(line == x for x in original_lines) for line in BRIDGE.splitlines())


@pytest.mark.parametrize('queue,task,status,expected', [('test', TASK_ID, 'pending', True),
    ('ctf_A', TASK_ID, 'pending', False), ('test', 'foreign', 'pending', False),
    ('test', TASK_ID, 'replied', False)])
def test_cli_hook_scope_before_legacy_emit_and_path_restored(monkeypatch, queue, task, status, expected):
    calls = []
    monkeypatch.setattr(native_notify, 'notify', lambda job: calls.append('notify') or True)
    module = {'selected_queue': queue, 'selected_task': task,
              'q': SimpleNamespace(put_message=lambda *a, **kw: {'job_id': 'test_fixture.job', 'status': status}),
              'bs': SimpleNamespace(BridgeApp=lambda *a: SimpleNamespace(emit_test_event=lambda *a: calls.append('emit')))}
    exec(compile(patch.rewrite(CLI, 'cli'), '<trusted-cli-fixture>', 'exec'), module)
    previous = list(sys.path)
    assert module['main']() == (0 if status == 'pending' else 1)
    assert sys.path == previous
    assert ('notify' in calls) == expected
    if expected: assert calls == ['notify', 'emit']


def test_cli_notify_failure_preserves_legacy_publication(monkeypatch):
    calls = []
    def fail(*a): raise RuntimeError('do not print')
    monkeypatch.setattr(native_notify, 'notify', fail)
    module = {'selected_queue': 'test', 'selected_task': TASK_ID,
              'q': SimpleNamespace(put_message=lambda *a, **kw: {'job_id': 'test_fixture.job', 'status': 'pending'}),
              'bs': SimpleNamespace(BridgeApp=lambda *a: SimpleNamespace(emit_test_event=lambda *a: calls.append(True)))}
    exec(compile(patch.rewrite(CLI, 'cli'), '<trusted-cli-fixture>', 'exec'), module)
    previous = list(sys.path)
    assert module['main']() == 0
    assert sys.path == previous and calls == [True]


@pytest.mark.parametrize('kind,source', [('bridge', BRIDGE), ('cli', CLI)])
def test_hash_guard_no_reapply(kind, source):
    with pytest.raises(ValueError): patch.candidate(source.encode(), kind)
    with pytest.raises(ValueError): patch.rewrite(patch.rewrite(source, kind), kind)


def test_cli_pending_guard_and_unique_shape_required():
    with pytest.raises(ValueError): patch.rewrite(CLI.replace("if r['status'] != 'pending': print(r); return 1", 'pass'), 'cli')
    with pytest.raises(ValueError): patch.rewrite(CLI.replace('    bridge =', "    another = q.put_message('extra')\n    r = q.put_message('again')\n    bridge ="), 'cli')


def test_cli_unrelated_bridge_constructor_is_not_the_report_anchor():
    source = CLI + "\ndef diagnostics():\n    bridge = bs.BridgeApp('unrelated')\n    return bridge\n"
    changed = patch.rewrite(source, 'cli')
    assert changed.count('from git_dialog.native_notify import notify') == 1
    assert changed.endswith("def diagnostics():\n    bridge = bs.BridgeApp('unrelated')\n    return bridge\n")
    assert changed.index("notify(r['job_id'])") < changed.index("n = bridge.emit_test_event")


def test_cli_duplicate_constructor_inside_report_block_still_rejected():
    source = CLI.replace("    n = bridge.emit_test_event", "    bridge = bs.BridgeApp('ambiguous')\n    n = bridge.emit_test_event")
    with pytest.raises(patch.ShapeError) as failure:
        patch.rewrite(source, 'cli')
    assert failure.value.phase == 'cli_bridge_anchor'


def test_cli_safe_shape_phase_without_source_or_exception_output(tmp_path, monkeypatch, capsys):
    source = CLI.replace("if r['status'] != 'pending': print(r); return 1", 'pass')
    path = tmp_path / 'source.py'
    path.write_text(source)
    path.chmod(0o644)
    monkeypatch.setitem(patch.FILES, 'cli', path)
    monkeypatch.setitem(patch.HASHES, 'cli', hashlib.sha256(path.read_bytes()).hexdigest())
    output = tmp_path / 'candidate.py'
    assert patch.main(['--kind', 'cli', '--output', str(output)]) == 1
    assert json.loads(capsys.readouterr().out) == {
        'error': 'notify_candidate_failed_no_live_write', 'phase': 'cli_pending_guard'}
    assert not output.exists()


def test_cli_safe_io_phase_does_not_print_exception(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(patch.FILES, 'cli', tmp_path / 'secret-config-do-not-print.py')
    assert patch.main(['--kind', 'cli', '--output', str(tmp_path / 'candidate.py')]) == 1
    assert json.loads(capsys.readouterr().out) == {
        'error': 'notify_candidate_failed_no_live_write', 'phase': 'source_read'}


@pytest.mark.parametrize('kind,source', [('bridge', BRIDGE), ('cli', CLI)])
def test_candidate_only_fixed_source_and_exclusive_output(tmp_path, monkeypatch, capsys, kind, source):
    path = tmp_path / 'source.py'
    path.write_text(source)
    path.chmod(0o644)
    output = tmp_path / 'candidate.py'
    monkeypatch.setitem(patch.FILES, kind, path)
    monkeypatch.setitem(patch.HASHES, kind, hashlib.sha256(path.read_bytes()).hexdigest())
    assert patch.main(['--kind', kind, '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result['live_source_modified']
    assert output.stat().st_mode & 0o777 == 0o600
    assert result['candidate_sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert path.read_text() == source
    before = output.read_bytes()
    assert patch.main(['--kind', kind, '--output', str(output)]) == 1
    assert output.read_bytes() == before


@pytest.mark.parametrize('bad', ['mode', 'hardlink', 'hash'])
def test_candidate_source_guards_fail_no_output(tmp_path, monkeypatch, bad):
    path = tmp_path / 'source.py'
    path.write_text(CLI)
    path.chmod(0o644)
    monkeypatch.setitem(patch.FILES, 'cli', path)
    monkeypatch.setitem(patch.HASHES, 'cli', hashlib.sha256(path.read_bytes()).hexdigest())
    if bad == 'mode': path.chmod(0o664)
    if bad == 'hardlink': (tmp_path / 'link').hardlink_to(path)
    if bad == 'hash': path.write_text(CLI + '# changed\n')
    output = tmp_path / 'candidate.py'
    assert patch.main(['--kind', 'cli', '--output', str(output)]) == 1
    assert not output.exists()

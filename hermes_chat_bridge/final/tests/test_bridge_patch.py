import ast
import hashlib

import pytest

from git_dialog import DialogError, bridge_patch as patch

SOURCE = '''from contextlib import asynccontextmanager
class BridgeApp:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.local_dirty_change = "PRESERVE_EXISTING_CHANGE"

    def emit_test_event(self, job_id, queue=None):
        payload = {"job_id": job_id}
        subs = self.store.active_subscriptions()
        return len(subs)

def build_app(base):
    app = existing_build(base)
    bridge = app.state.bridge
    pilot_lock = app.state.pilot_lock
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as state:
                yield state
        finally:
            pilot_lock.close()
    app.router.lifespan_context = lifespan
    return app
'''


def test_transform_preserves_local_lines_and_original_delivery():
    result = patch._rewrite(SOURCE)
    ast.parse(result)
    assert 'PRESERVE_EXISTING_CHANGE' in result
    assert 'subs = self.store.active_subscriptions()' in result
    assert 'return len(subs)' in result
    assert result.index('self._beta_forwarder = None') < result.index('def enable_beta_wakeup')
    assert result.index('self._beta_forwarder.offer(payload)') < result.index('subs =')
    assert result.index('bridge._beta_forwarder.start()') < result.index('async with original_lifespan')
    assert result.index('bridge._beta_forwarder.stop()') < result.index('pilot_lock.close()')
    assert result.count('pilot_lock.close()') == 1


def test_real_hash_guard_not_bypassed_by_fixture():
    with pytest.raises(DialogError, match='bridge_patch_source_changed'):
        patch.candidate(SOURCE.encode())


@pytest.mark.parametrize('change', ['class BridgeApp:', 'def emit_test_event', 'def build_app',
                                  'async def lifespan', 'pilot_lock.close()'])
def test_shape_changes_fail_closed(change):
    broken = SOURCE.replace(change, change.replace('BridgeApp', 'Other').replace('emit_test_event', 'other')
                            .replace('build_app', 'other').replace('lifespan', 'other').replace('pilot_lock', 'other'))
    with pytest.raises(DialogError): patch._rewrite(broken)


def test_patch_not_reapplied():
    with pytest.raises(DialogError, match='bridge_patch_already_present'):
        patch._rewrite(patch._rewrite(SOURCE))


def test_cli_candidate_only_no_overwrite(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'bridge_server.py'; source.write_text(SOURCE)
    output = tmp_path / 'candidate.py'
    monkeypatch.setattr(patch, 'EXPECTED', hashlib.sha256(SOURCE.encode()).hexdigest())
    assert patch.main(['--source', str(source), '--output', str(output)]) == 0
    before = output.read_bytes()
    assert source.read_text() == SOURCE
    assert patch.main(['--source', str(source), '--output', str(output)]) == 1
    assert output.read_bytes() == before and source.read_text() == SOURCE
    assert 'live_source_modified' in capsys.readouterr().out


def test_source_changed_no_output(tmp_path):
    source = tmp_path / 'source'; source.write_text(SOURCE)
    output = tmp_path / 'new'
    assert patch.main(['--source', str(source), '--output', str(output)]) == 1
    assert not output.exists()


def test_symlink_source_rejected(tmp_path):
    source = tmp_path / 'source'; source.write_text(SOURCE)
    link = tmp_path / 'link'; link.symlink_to(source)
    assert patch.main(['--source', str(link), '--output', str(tmp_path / 'new')]) == 1
    assert source.read_text() == SOURCE

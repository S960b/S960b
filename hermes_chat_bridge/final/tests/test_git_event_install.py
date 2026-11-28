import hashlib

import pytest

from git_dialog import DialogError
from git_dialog import git_event_install as patch

SOURCE = '''class Store:
    def active_subscriptions(self): pass
    def enqueue_delivery(self, sub_id, event_id, payload): pass
class BridgeApp:
    def emit_test_event(self, job_id, queue=None):
        for s in self.native_state.active_subscriptions():
            self.native_state.enqueue_delivery(s['id'], 'event', {})
def build_app(base_url):
    # PRESERVE EXISTING DIRTY LINES
    install_gateway_lifespan(app, bridge, dialog_factory=dialog_factory_from_systemd)
    return app
'''


@pytest.fixture
def paths(tmp_path, monkeypatch):
    base, directory = tmp_path/'base', tmp_path/'source'
    base.mkdir(mode=0o700); directory.mkdir(mode=0o700)
    source = directory/'bridge.py'; source.write_text(SOURCE); source.chmod(0o644)
    for key, val in {'BASE':base, 'SOURCE':source, 'BACKUP':base/'backup', 'STAGE':directory/'.stage',
        'ARCHIVE':base/'archive', 'RESTORE':directory/'.restore',
        'BEFORE':hashlib.sha256(SOURCE.encode()).hexdigest()}.items(): monkeypatch.setattr(patch, key, val)
    return source


def test_binding_uses_actual_static_receiver_not_guessed_service_attribute():
    result = patch.rewrite(SOURCE)
    assert result.replace(', notification_store=bridge.native_state', '') == SOURCE
    assert 'bridge.store' not in result and 'require_owner' not in result


@pytest.mark.parametrize('changed', [
    SOURCE.replace('self.native_state.enqueue_delivery', 'self.other.enqueue_delivery'),
    SOURCE.replace('self.native_state.active_subscriptions()', 'get_store().active_subscriptions()'),
    SOURCE.replace('payload):', 'payload, extra=None):'),
    SOURCE.replace('    return app', '    install_gateway_lifespan(app, bridge)\n    return app'),
    SOURCE.replace('dialog_factory=dialog_factory_from_systemd', 'dialog_factory=other'),
    SOURCE.replace('\n', '\r\n')])
def test_changed_contract_stops_without_runtime_introspection(changed):
    with pytest.raises(DialogError): patch.rewrite(changed)


def test_hash_pin_no_reapply_and_new_private_backup_then_exact_rollback(paths):
    before = paths.read_bytes()
    assert patch.run('check')['status'] == 'git_event_native_checked'
    assert not patch.BACKUP.exists()
    result = patch.run('install'); after = paths.read_bytes()
    assert result['completed_replacements'] == ['bridge'] and not result['service_action']
    assert patch.BACKUP.read_bytes() == before and patch.BACKUP.stat().st_mode & 0o777 == 0o600
    assert paths.stat().st_mode & 0o777 == 0o644
    assert patch.run('install')['error'] == 'git_event_install_failed'
    assert patch.run('rollback')['status'] == 'git_event_native_restored'
    assert paths.read_bytes() == before and patch.ARCHIVE.read_bytes() == after


@pytest.mark.parametrize('name', ['BACKUP','STAGE','ARCHIVE','RESTORE'])
def test_occupied_artifacts_are_never_reused_or_removed(paths, name):
    path = getattr(patch, name); path.write_bytes(b'preserve')
    result = patch.run('install')
    assert result['completed_replacements'] == [] and paths.read_text() == SOURCE
    assert path.read_bytes() == b'preserve'


def test_postrename_failure_retains_exact_partial_facts(paths, monkeypatch):
    original = patch._sync
    def sync(path):
        if path == paths.parent and 'notification_store=' in paths.read_text() and not patch.STAGE.exists():
            raise OSError('private details')
        original(path)
    monkeypatch.setattr(patch, '_sync', sync)
    result = patch.run('install')
    assert result == {'error':'git_event_install_failed', 'phase':'directory_sync',
                      'completed_replacements':['bridge'], 'service_action':False}
    assert patch.BACKUP.exists()


@pytest.mark.parametrize('before,after,guard', [
    ('class BridgeApp:', 'class Other:', 'bridge_class'),
    ('class Store:', 'class Other:', 'store_class'),
    ('def emit_test_event(', 'def other_event(', 'emit_method'),
    ('def active_subscriptions(self):', 'def active_subscriptions(self, owner):', 'active_subscriptions_signature'),
    ('def enqueue_delivery(self, sub_id, event_id, payload):',
     'def enqueue_delivery(self, sub_id, event_id, payload, extra):', 'enqueue_delivery_signature'),
    ('self.native_state.enqueue_delivery', 'self.other.enqueue_delivery', 'store_receiver'),
    ('dialog_factory=dialog_factory_from_systemd', 'dialog_factory=other', 'lifespan_arguments'),
])
def test_failure_reports_only_exact_static_guard_name(paths, monkeypatch, before, after, guard):
    source = SOURCE.replace(before, after)
    paths.write_text(source)
    monkeypatch.setattr(patch, 'BEFORE', hashlib.sha256(source.encode()).hexdigest())
    result = patch.run('check')
    assert result == {'error':'git_event_install_failed','phase':'shape',
                      'completed_replacements':[],'service_action':False,'guard':guard}
    assert not patch.BACKUP.exists() and paths.read_text() == source


def test_actual_p26b_store_headers_receiver_and_unmodified_lifespan_call():
    # Public structural facts from the hash-guarded MT report only. No native
    # import, original body/config/default values or environment is copied.
    source = '''class Store:
    def active_subscriptions(self) -> list[dict]: pass
    def enqueue_delivery(self, sub_id: str, event_id: str, payload: dict): pass
class BridgeApp:
    def emit_test_event(self, job_id: str, queue: str | None = None):
        for s in self.store.active_subscriptions():
            self.store.enqueue_delivery(s['id'], event_id, payload)
def build_app(base_url):
    from git_dialog.gateway_lifespan import install_lifespan as install_gateway_lifespan
    from git_dialog.gateway_credentials import dialog_factory_from_systemd
    install_gateway_lifespan(app, bridge, dialog_factory=dialog_factory_from_systemd)
    return app
'''
    result = patch.rewrite(source)
    assert result.replace(', notification_store=bridge.store', '') == source
    assert result.count('notification_store=') == 1
    with pytest.raises(patch.ShapeError) as error: patch.rewrite(source.replace('class Store:', 'class BridgeStore:'))
    assert error.value.guard == 'store_class'
    with pytest.raises(patch.ShapeError): patch.rewrite(result)

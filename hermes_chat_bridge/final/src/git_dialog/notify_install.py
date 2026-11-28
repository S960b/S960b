"""P15 fixed two-source install/rollback; no activation, credential or service I/O."""
import argparse
import hashlib
import json
from pathlib import Path
import os

from .native_install import _absent, _directory, _new, _read, _sync

DIRECTORY = Path('/home/kali/S960b/research/hermes_chat_bridge')
BASE = Path('/home/kali/agent-dialog-beta')
STATE = BASE / 'state'
MARKER = STATE / 'bridge-wakeup.enabled'
KINDS = ('bridge', 'cli')
SOURCES = {'bridge': DIRECTORY / 'bridge_server.py', 'cli': DIRECTORY / 'bridge_cli.py'}
BEFORE = {
    'bridge': '4921809e5aa9e7a67f7bee77f355d58123b076507d9d8f54da6b67dd4451c585',
    'cli': '0d2cdcb6e034b05a3526c55d7dd0efa2cead886b2fdc82b6f9d3165272e63d67',
}
AFTER = {
    'bridge': '2714aca2a7420cd2ff073baee498302f63ade28e49051ca546c69b62ddbb9ce5',
    'cli': '837ba7279b4f523c53d44a0139f9e5e3d539bf96c9821714d7bed5586d0e1048',
}
BACKUPS = {kind: BASE / (kind + '-before-notify.p15.py') for kind in KINDS}
ARCHIVES = {kind: BASE / (kind + '-notify.p15-rollback.py') for kind in KINDS}
STAGES = {kind: DIRECTORY / ('.' + kind + '-notify.p15-stage') for kind in KINDS}
RESTORES = {kind: DIRECTORY / ('.' + kind + '-notify.p15-restore') for kind in KINDS}
EMPTY = hashlib.sha256(b'').hexdigest()


def _directories():
    _directory(DIRECTORY)
    _directory(BASE, 0o700)
    _directory(STATE, 0o700)


def _unchanged(hashes, identities, marker_identity):
    _directories()
    if _read(MARKER, EMPTY, 0o600)[1] != marker_identity:
        raise ValueError('marker_changed')
    for kind in KINDS:
        if _read(SOURCES[kind], hashes[kind], 0o644)[1] != identities[kind]:
            raise ValueError('source_changed')


def run(action, bridge_candidate=None, cli_candidate=None):
    """Serialized operator use, not a two-file atomic transaction or hostile-writer CAS.

    All backups/stages are prepared before the first replacement. Failures retain
    every partial artifact and report completed replacements, without retry,
    cleanup or automatic rollback. Full rollback requires BOTH installed hashes;
    a partial installation needs a separately reviewed recovery, never a rerun.
    """
    phase, completed = 'options', []
    try:
        if (action not in ('check', 'install', 'rollback') or
                (action == 'rollback' and (bridge_candidate is not None or cli_candidate is not None)) or
                (action in ('check', 'install') and (bridge_candidate is None or cli_candidate is None))):
            raise ValueError('options_rejected')
        phase = 'guards'
        _directories()
        _, marker_identity = _read(MARKER, EMPTY, 0o600)
        hashes = dict(AFTER if action == 'rollback' else BEFORE)
        current = {kind: _read(SOURCES[kind], hashes[kind], 0o644) for kind in KINDS}
        identities = {kind: current[kind][1] for kind in KINDS}
        if action == 'rollback':
            target = {kind: _read(BACKUPS[kind], BEFORE[kind], 0o600)[0] for kind in KINDS}
            target_hashes, stages = BEFORE, RESTORES
            _absent(*ARCHIVES.values(), *STAGES.values(), *RESTORES.values())
        else:
            target = {}
            for kind, candidate in zip(KINDS, (bridge_candidate, cli_candidate)):
                path = Path(candidate)
                _directory(path.parent)
                target[kind] = _read(path, AFTER[kind], 0o600)[0]
            target_hashes, stages = AFTER, STAGES
            _absent(*BACKUPS.values(), *ARCHIVES.values(), *STAGES.values(), *RESTORES.values())
        _unchanged(hashes, identities, marker_identity)
        if action == 'check':
            return {'status': 'notify_install_checked', 'live_sources_modified': False,
                    'activation_modified': False, 'service_restarted': False}
        retained = ARCHIVES if action == 'rollback' else BACKUPS
        for kind in KINDS:
            phase = kind + ('_archive' if action == 'rollback' else '_backup')
            _new(retained[kind], current[kind][0], 0o600)
            _read(retained[kind], hashes[kind], 0o600)
        for kind in KINDS:
            phase = kind + '_stage'
            _new(stages[kind], target[kind], 0o644)
            _read(stages[kind], target_hashes[kind], 0o644)
        # Install bridge before CLI; restore CLI before bridge. This is NOT atomic.
        order = tuple(reversed(KINDS)) if action == 'rollback' else KINDS
        for kind in order:
            phase = kind + '_replace'
            _unchanged(hashes, identities, marker_identity)
            for item in KINDS:
                _read(retained[item], (AFTER if action == 'rollback' else BEFORE)[item], 0o600)
                if action == 'rollback':
                    _read(BACKUPS[item], BEFORE[item], 0o600)
            _read(stages[kind], target_hashes[kind], 0o644)
            os.replace(stages[kind], SOURCES[kind])
            completed.append(kind)  # Includes a completed rename even if fsync fails.
            _sync(DIRECTORY)
            phase = kind + '_verify'
            hashes[kind] = target_hashes[kind]
            identities[kind] = _read(SOURCES[kind], hashes[kind], 0o644)[1]
        phase = 'final_verify'
        _unchanged(hashes, identities, marker_identity)
        return {'status': 'notify_pair_restored' if action == 'rollback' else 'notify_pair_installed',
                'completed_replacements': completed, 'retained_artifacts_verified': True,
                'activation_modified': False, 'service_restarted': False}
    except Exception:
        return {'error': 'notify_install_failed', 'action': action if action in
                ('check', 'install', 'rollback') else 'invalid', 'phase': phase,
                'completed_replacements': completed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'install', 'rollback'])
    parser.add_argument('--bridge-candidate')
    parser.add_argument('--cli-candidate')
    args = parser.parse_args(argv)
    result = run(args.action, args.bridge_candidate, args.cli_candidate)
    print(json.dumps(result))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())

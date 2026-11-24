#!/usr/bin/env python3
"""Read-only copy of committed bridge source and MT plugin into a separate clone.

No checkout, reset, stash, push, service or deployment operation is performed here.
Dest MUST be an isolated clone of S960b/agent-dialogs migration/pg-v2-source.
The script deliberately fails closed on likely embedded credentials.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

SOURCE = Path('/home/kali/agent-dialog-beta/code')
PLUGIN = Path('/home/kali/.config/gigatool/plugins/mt-git-inbox.mjs')
REV = '755d1c1'
ALLOW_EXT = {'.py', '.html', '.js', '.css', '.json', '.md'}
MAX_FILE = 200_000
MAX_TOTAL = 2_500_000
FORBID_PATH = re.compile(r'(^|/)(?:\.env(?:\..*)?|\.git|.*(?:secret|token|password|private[-_]?key|credential).*|.*\.pem|.*\.key)(/|$)', re.I)
FORBID_CONTENT = re.compile(
    rb'(?i)(?:ghp_[a-z0-9]{30,}|github_pat_[a-z0-9_]{30,}|sk-(?:proj-)?[a-z0-9_-]{30,}|'
    rb'xox[baprs]-[a-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|'
    rb'(?:password|secret|api_key|access_token|refresh_token)\s*[:=]\s*[\x22\x27][^\x22\x27\r\n]{12,}[\x22\x27])'
)


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(SOURCE), *args], stderr=subprocess.DEVNULL)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: export_current_sources.py /path/to/isolated-clone')
    dest = Path(sys.argv[1]).resolve(strict=True)
    if not (dest / '.git').is_dir() or SOURCE.resolve() == dest:
        raise SystemExit('REFUSING: destination must be a separate cloned Git repository')
    dest_head = subprocess.check_output(['git', '-C', str(dest), 'branch', '--show-current'], text=True).strip()
    if dest_head != 'migration/pg-v2-source':
        raise SystemExit('REFUSING: destination not on migration/pg-v2-source')
    if (dest / 'migration-source').exists():
        raise SystemExit('REFUSING: migration-source directory already exists')
    if git('rev-parse', '--verify', REV + '^{commit}').strip() == b'':
        raise SystemExit('Source revision missing')

    names = git('ls-tree', '-r', '--name-only', REV, '--', 'src/git_dialog').decode().splitlines()
    candidates: dict[str, bytes] = {}
    for name in names:
        p = Path(name)
        if (not name.startswith('src/git_dialog/') or p.suffix not in ALLOW_EXT
                or FORBID_PATH.search(name)):
            continue
        blob = git('show', REV + ':' + name)
        candidates[name] = blob
    if not PLUGIN.is_file() or not PLUGIN.is_symlink() and PLUGIN.stat().st_size > MAX_FILE:
        raise SystemExit('Missing or oversized MT plugin; refusing incomplete export')
    if PLUGIN.is_symlink():
        raise SystemExit('REFUSING: plugin is a symlink')
    candidates['plugins/mt-git-inbox.mjs'] = PLUGIN.read_bytes()

    if not candidates or 'src/git_dialog/native_gateway.py' not in candidates:
        raise SystemExit('REFUSING: native gateway absent from export')
    issues: list[str] = []
    total = 0
    for name, blob in candidates.items():
        total += len(blob)
        if len(blob) > MAX_FILE or b'\x00' in blob:
            issues.append(name + ': unsupported size or binary data')
        if FORBID_CONTENT.search(blob):
            issues.append(name + ': possible literal credential found')
    if total > MAX_TOTAL:
        issues.append('total size exceeds limit')
    if issues:
        print('EXPORT ABORTED: manual security review required. Only filenames follow:')
        print('\n'.join(issues))
        raise SystemExit(3)

    out = dest / 'migration-source'
    out.mkdir(mode=0o700)
    manifest = []
    for name, blob in sorted(candidates.items()):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        manifest.append({'file': name, 'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()})
    (out / 'MANIFEST.json').write_text(json.dumps({
        'source_revision': REV, 'files': manifest,
        'notice': 'Source export for migration. No runtime data, database or credentials.'
    }, indent=2, ensure_ascii=False) + '\n')
    print(f'EXPORT_OK files={len(manifest)} bytes={total} dir={out}')
    print('No deployment was performed and current live working tree was not changed.')


if __name__ == '__main__':
    main()

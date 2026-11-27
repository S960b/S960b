#!/usr/bin/env python3
"""Extract NeuroPet .ab without Android tools (stdlib only).
Usage: python3 scripts/01_unpack_android_backup.py neuropet.ab --out extracted
"""
import argparse
import hashlib
import io
from pathlib import Path
import tarfile
import zipfile
import zlib


def load_ab(path):
    blob = Path(path).read_bytes()
    parts = blob.split(b'\n', 4)
    if len(parts) != 5 or parts[0] != b'ANDROID BACKUP':
        raise ValueError('Not an Android Backup (.ab) file')
    _, version, compressed, encryption, payload = parts
    if encryption != b'none':
        raise ValueError('Encrypted .ab requires its backup password')
    return dict(version=version.decode(), compressed=compressed == b'1',
                encryption=encryption.decode()), (zlib.decompress(payload) if compressed == b'1' else payload)


def safe_copy_tar_member(tar, m, root):
    if not m.isfile():
        return None
    root = root.resolve()
    dest = (root / m.name).resolve()
    if not dest.is_relative_to(root):
        raise ValueError('Unsafe tar path: ' + m.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    f = tar.extractfile(m)
    if f is None:
        raise RuntimeError('Could not extract ' + m.name)
    dest.write_bytes(f.read())
    return dest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('backup')
    p.add_argument('--out', default='neuro_extracted')
    a = p.parse_args()
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    metadata, contents = load_ab(a.backup)
    print('Android Backup:', metadata)
    print('TAR size:', len(contents))
    archive = root / 'neuropet_backup.tar'
    archive.write_bytes(contents)
    with tarfile.open(fileobj=io.BytesIO(contents), mode='r:') as tar:
        for member in tar:
            dest = safe_copy_tar_member(tar, member, root)
            if dest:
                print('TAR:', member.size, member.name)
    apks = list(root.rglob('base.apk'))
    if len(apks) != 1:
        raise RuntimeError(f'Expected 1 APK, found {len(apks)}')
    apk = root / 'base.apk'
    apk.write_bytes(apks[0].read_bytes())
    with zipfile.ZipFile(apk) as z:
        z.extractall(root / 'apk_contents')
        print('\nAPK interesting entries:')
        for info in z.infolist():
            if info.filename.startswith(('assets/neuro/', 'lib/')) or info.filename == 'classes.dex':
                print(f'  {info.file_size:7d} {info.filename}')
    print('\nAPK:', apk)
    print('APK SHA256:', hashlib.sha256(apk.read_bytes()).hexdigest())
    print('Next: python3 scripts/02_solve_neurovm.py', apk, '--out', root / 'vm_recovered')


if __name__ == '__main__':
    main()

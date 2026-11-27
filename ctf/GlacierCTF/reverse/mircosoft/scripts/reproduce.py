#!/usr/bin/env python3
"""Reproduce GlacierCTF mircosoft from the organizer .tar.gz in one command.

python3 scripts/reproduce.py 'mircosoft.tar.gz'
Uses only Python 3.10+ standard library; no Windows executable is run.
"""
from __future__ import annotations
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
INNER_SHA256 = "9d5df8e2717f29b5ee8af7a0b2c423de754aac7676e65c4429bc74361f191d80"

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/reproduce.py PATH_TO_ORIGINAL_TAR_GZ")
    original = pathlib.Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="mircosoft_ctf_") as temporary:
        work = pathlib.Path(temporary)
        with tarfile.open(original, "r:gz") as tar:
            tar.extractall(work, filter="data")
        source = work / "mircosoft"
        for p in ("mircosoft.exe", "license.bin", "admin_machine.dmp"):
            if not (source / p).is_file():
                raise SystemExit("Missing organizer file: " + p)
        unpacked = work / "unpacked_pe.bin"
        subprocess.run([sys.executable, str(HERE/"unpack.py"),
                        str(source/"mircosoft.exe"), "-o", str(unpacked)], check=True)
        result_hash = hashlib.sha256(unpacked.read_bytes()).hexdigest()
        if result_hash != INNER_SHA256:
            raise SystemExit("Unexpected unpacked PE SHA256: " + result_hash)
        subprocess.run([sys.executable, str(HERE/"extract_minidump.py"),
                        str(source/"admin_machine.dmp"), "--out-dir", str(work)], check=True)
        env = dict(os.environ, MIRCOSOFT_WORKDIR=str(work))
        output = subprocess.run([sys.executable, str(HERE/"solve.py")],
                                env=env, capture_output=True, text=True, check=True)
        print(output.stdout)
        match = re.search(r"header mac ([0-9a-f]{32}) vs ([0-9a-f]{32})", output.stdout)
        if not match or match.group(1) != match.group(2):
            raise SystemExit("License fingerprint MAC verification FAILED")
        if not re.search(r"plaintext b'gctf\{[^']+\}", output.stdout):
            raise SystemExit("Final decrypted flag not found")
        print("REPRODUCTION OK: original archive -> unpacked PE -> MiniDump -> verified license -> flag")

if __name__ == "__main__":
    main()

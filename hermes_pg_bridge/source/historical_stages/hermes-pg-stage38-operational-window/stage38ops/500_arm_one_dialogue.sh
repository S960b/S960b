#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
sha256sum -c SHA256SUMS
/usr/bin/python3 -m unittest -q test_ops38.py
/usr/bin/python3 -IB "$HERE/ops38.py" arm

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ "$(id -un)" == kali && "$HOME" == /home/kali ]] || { echo STAGE37B2F1_STOP_USER; exit 2; }
cd "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c SHA256SUMS
python3 -m unittest -q test_verify37b2f1.py
python3 -IB verify37b2f1.py

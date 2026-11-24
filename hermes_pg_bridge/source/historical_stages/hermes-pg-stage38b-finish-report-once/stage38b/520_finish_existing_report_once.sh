#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")"
sha256sum -c SHA256SUMS
python3 -E -s -B -m unittest discover -q -p test_stage38b.py
# Isolated mode: runner imports the pinned Stage38 armer explicitly by path.
python3 -I -B ./finish38b.py

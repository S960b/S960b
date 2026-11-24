#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"
sha256sum -c SHA256SUMS
python3 -m unittest discover -b -q -p 'test_stage38c.py'
# -I is safe: the script uses importlib.spec_from_file_location for sibling code.
exec python3 -IB "$BASE/finish38c.py"

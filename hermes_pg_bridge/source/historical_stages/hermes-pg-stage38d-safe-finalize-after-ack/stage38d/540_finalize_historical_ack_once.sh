#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"
sha256sum -c SHA256SUMS
# -I is safe: dynamically loaded adjacent modules are imported via absolute file paths.
exec python3 -IB "$BASE/finalize38d.py"

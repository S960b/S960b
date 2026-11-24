#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"
sha256sum -c SHA256SUMS
exec python3 -IB "$BASE/finalize38c.py"

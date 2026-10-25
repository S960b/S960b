#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/solution/reproduce.sh" "$HERE/original/voteme-27f2656eaea4fcf2.tar.gz"
echo "Output: $HERE/solution/voteme_work/flag.txt"

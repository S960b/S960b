#!/usr/bin/env bash
# Demonstrates why spaces are fatal in the unquoted let expansion.
# No network, flags, or external services. Uses a temporary marker.
set -e
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
export QA_FILE="$tmpdir/marker"

bad='x[$(: >"$QA_FILE")]'
i=$bad
let i=$i-1 2>/dev/null || true
if test -e "$QA_FILE"; then
  echo "FAIL: space-containing input unexpectedly executed" >&2
  exit 1
fi

good='x[$(:>"$QA_FILE")]'
i=$good
let i=$i-1
test -f "$QA_FILE"
echo 'PASS: word-split version fails, whitespace-free version executes command substitution'

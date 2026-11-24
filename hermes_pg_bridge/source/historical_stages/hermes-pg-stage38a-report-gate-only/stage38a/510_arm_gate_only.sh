#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# The script is self-contained: Python -I works on 3.11–3.14, no sibling imports.
exec python3 -IB "$(dirname -- "${BASH_SOURCE[0]}")/510_arm_gate_only.py"

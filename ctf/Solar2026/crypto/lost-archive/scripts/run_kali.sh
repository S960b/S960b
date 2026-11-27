#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# User-local installation, works around Kali's externally managed system Python.
if ! PYTHONPATH="$PWD/deps${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'import flatn; import sympy' >/dev/null 2>&1; then
    python3 -m pip install --target "$PWD/deps" 'flatn==0.3.5' 'sympy>=1.12'
fi
export PYTHONPATH="$PWD/deps${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
python3 solve_rsa_partial.py --selftest
exec python3 solve_rsa_partial.py --input output.txt --guess-bits 5 --workers 4 "$@"

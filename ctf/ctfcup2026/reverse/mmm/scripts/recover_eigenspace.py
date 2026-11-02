#!/usr/bin/env python3
"""Exact, exploratory 40x40 eigenvalue-1337 analysis.

Input: matrix.json containing 40 rows of 40 integers extracted from the
challenge ELF. The original matrix is not bundled in this repository.
This is an analysis helper, not a hardcoded flag generator.
"""
import json
import sys
from pathlib import Path

try:
    from sympy import Matrix, eye
except ImportError as exc:
    raise SystemExit("Install SymPy with: python3 -m pip install sympy") from exc


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 recover_eigenspace.py matrix.json")
    rows = json.loads(Path(sys.argv[1]).read_text())
    if len(rows) != 40 or any(len(row) != 40 for row in rows):
        raise SystemExit("Expected a 40x40 integer matrix")
    if any(not isinstance(v, int) or isinstance(v, bool) for row in rows for v in row):
        raise SystemExit("Matrix elements must be integers")
    matrix = Matrix(rows)
    kernel = (matrix - 1337 * eye(40)).nullspace()
    print("Nullspace dimension:", len(kernel))
    for i, vector in enumerate(kernel):
        assert matrix * vector == 1337 * vector
        print("Basis", i, ":", [str(value) for value in vector])


if __name__ == "__main__":
    main()

# Cold Echo original solver pipeline

This folder contains the original historical Python pipeline, transcribed from the participant-provided evidence archive. The code matches the successful method; `roots.py` and `cz.py` have minor cleanup of redundant expressions and an extra division assertion, preserving their algorithms.

Run from a **single working directory** containing the organizer's `archive.json` (extract `cold-echo.tar.gz` from the private evidence ZIP or the competition handout), and the Python scripts:

```sh
python3 -m pip install ecdsa
python3 lin.py       # -> lin.json
python3 sbuild.py    # -> S.json
python3 roots.py     # -> h.json
python3 cz.py        # -> roots.json, randomized Cantor–Zassenhaus
python3 pairs.py     # -> pairs.json
python3 mitm.py      # -> sols.json (large memory usage)
python3 solve.py     # -> signed receipt and FLAG
```

The scripts use local relative paths such as `open('archive.json')`; run them in the same directory as `archive.json`. Only the final `solve.py` requires the third-party `ecdsa` library; the rest run on the Python standard library. `solve_lin_FAILED_sympy.py` documents a **superseded, slow** attempt and must not be run as part of the successful pipeline.

The organizer's full input data and all successful intermediate JSON files are retained in the private `glacierctf_cold-echo_materials.zip`. The public repository deliberately avoids publishing the unredacted final result and exact signed polynomial. This is a fully offline puzzle — no API keys or CTF server needed.

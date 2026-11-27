# Sensory Box scripts

The participant's private archive `glacierctf_sensory_box_materials.zip` preserves the exact successful `scripts/leak_via_mtime2.py` and a separate `scripts/solve_final_portable.py`. These are historical artifacts, not guesses.

The original script uses `sys.path.insert(0, '/tmp/gigatool')` and imports a local `pow` module. The portable copy instead references a bundled `vendor/kctf_pow.py`. In the evidence archive its import path needs an adjustment to resolve that nested file; the public [solve.py](solve.py) explicitly adds `vendor/` to sys.path.

`solve.py` requires Python 3, pwntools and the organizer's Google kCTF PoW helper (or the archived `vendor/kctf_pow.py`). Put `kctf_pow.py` in a `vendor/` directory next to `solve.py`. It connects to the historical service only when invoked. The public code uses `localhost` as its default target; historical host/port may no longer be live.

The original source and report, intermediate `mtime_marker.py`, `age_dec.py`, `get_bundle.py`, `play_box1.py`, and failed KDF tests remain in the private bundle. No live AGE keys, proof-of-work tokens, or full flag are published.

The original answer appears in `FLAG.txt` and the success trace in `evidence/final_run_output.txt`. The original resulting tar was not separately retained.

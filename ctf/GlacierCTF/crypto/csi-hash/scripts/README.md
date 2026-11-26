# Historical CSI-HASH research sources

The user-provided private `glacierctf_csi-hash_materials.zip` preserves the **original** working scripts, rather than just their filenames:

| File in evidence archive | Purpose | Status |
| --- | --- | --- |
| `scripts/get_logs2.py` | `quadclassunit`, `bnfinit`, 32 `bnfisprincipal` calls, checkpoints | **Executed successfully**, ~53 min |
| `scripts/csi_logs.json` | (p), class invariants, logs of all 32 prime ideals | **Saved output** |
| `scripts/csi_lll_fixed.py` | corrected kernel, fpylll, exact quadratic-form checks, TCP | **Executed successfully, flag recovered** |
| `scripts/csi_lll_fixed_from_downloads.py` | byte-identical copy of the successful solver | Preserved |
| `scripts/toy2.py` | 8-prime miniature consistency test | Executed |
| `scripts/solve_csi_hash.sage` | original proposed Sage approach | **Never executed** (Sage unavailable) |
| `scripts/csi_lll.py` | initial row/column-inverted kernel | Superseded |
| `scripts/get_logs.py` | first log collector with invalid `bnf_get_cyc` call | Superseded |
| `scripts/versions.py`, `scale.py`, `scale2.py` | environment and performance experiments | Executed |

Environment: Kali Linux / Python 3.13.3 / PARI 2.19.1 / cypari2 2.2.7 / fpylll 0.6.4. Agent workspace: `/tmp/gigatool/csi`; venv: `.v13`. Real `bnfinit` time **3164.0 s**, because the 177-bit class group is expensive. Running that computation **after** connecting to the 180-second challenge is impossible.

The working solver used a cache of all 32 ideal logarithms, then found the simple `(-3,...,-3)` relation. That universal relation is embedded in the smaller [solve.py](solve.py) so no heavyweight dependencies are needed to replay the final attack on a new legitimate challenge.

Local mathematical verification from the archived source used PARI forms `Qfb(1,0,p)` and `Qfb(ell, ±2, (p+1)/ell)` with `qfbcomp/qfbpow/qfbred`. The archived `csi_fixed.log` asserts that **all 32** LLL basis vectors pass the exact-order check.

No full flag or expired service session data is copied to GitHub. The original scripts and raw logs remain intact inside the supplied ZIP.

# Cold Echo: evidence and verification

The private user-supplied `glacierctf_cold-echo_materials.zip` contains original input, historical Python pipeline, and all intermediate checkpoints.

| Private file | Contents |
| --- | --- |
| `REPORT.md` | contemporary problem analysis and mathematical reasoning |
| `FLAG.txt` | real platform-accepted flag, deliberately omitted from public repo |
| `evidence/lin.json` | particular solution and one-dimensional kernel of 42×43 modular system |
| `evidence/S.json` | coefficients of reciprocal degree-84 polynomial; λ=1922965990 |
| `evidence/h.json` | gcd(S,X^p-X), degree 84 |
| `evidence/roots.json` | all 84 distinct finite-field roots |
| `evidence/pairs.json` | 42 pairs r and 1/r |
| `evidence/sols.json` | 243 MITM masks satisfying P(89) |
| `evidence/cold_echo_result.json` | complete signed message, coefficients P and recovered flag |
| `evidence/solver_run.log` | **repeated deterministic** full solver output; prints found: True, the original receipt, and FLAG |
| `evidence/original_archive.sha256` | SHA-256 of organizer tarball |

## Validation chain

```text
42 modular trace equations, rank 42
  -> S(1)=1 fixes λ=1922965990
  -> S degree 84, squarefree, splits completely over F_p
  -> 84 roots in 42 reciprocal pairs
  -> MITM 243 candidate root selections at t=89
  -> one ECDSA-valid signed receipt
  -> SHA-256 pad XOR -> urlsafe Base64 -> gctf{...}
```

Original final `scripts/solve.py` was **executed** by the investigator, and the exact original output was **rerun during evidence packaging**. No platform screenshot is included in the archived research materials; the participant confirms that the recovered flag was accepted.

This is an **offline** puzzle. There was no remote exploit or challenge server response to preserve. All numerical results and local computational details can be checked with the original data and scripts in the private bundle. The full recovered flag is masked in public GitHub under the established write-up policy.

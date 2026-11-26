# Evidence and limitations

Basis: `glacierctf_el-clasico_materials.zip`, assembled on November 27, 2026 from the November 21 Kali session and the earlier ChatGPT/Git-bridge task.

## Preserved artifacts

- `REPORT.md`: chronology and technical explanation, including refuted initial hypotheses.
- `FLAG.txt`: actual recovered flag, not republished.
- `evidence/solve_run.log`: transcribed stdout/stderr of successful solver run. It records **192,987 constraints**, FNV-1a hint IDs, a successful local `groth16.Verify`, and **HTTP 200**, whose literal response includes the real `gctf{...}` flag.
- `evidence/submit_request.json`: serialized successful request (public scalars and `proof_hex`).
- `evidence/verification_checks.md`: scalar congruence, curve test, challenge equation and other actual numerical tests.
- `evidence/search_params.log`: lattice search, 12th-degree polynomial, five roots.
- `evidence/ratiohint_trace_gnark.json`: 140 hint events used to validate the corrected GLV simulator.
- `scripts/solver_main.go`: original successful Go prover.
- `scripts/search_glv.py`: successful Python mathematical search.
- `assets/target.json`: saved live target.
- `assets/el-clasico.tar.gz`: original organizer archive, SHA-256 in [../assets/README.md](../assets/README.md).

## Verification chain

```text
choose legal (s1,s2) via GLV lattice
→ create doubleAndAdd collision
→ override 0/0 ratioHint slope
→ prove original challenge circuit with original pk.hex
→ local Verify with original vk.hex: OK
→ POST /submit: HTTP 200, Flag is: gctf{...}
```

The user-provided archive includes no screenshot of the accepted response and no live server access is assumed. The run log is a **transcription** made from the MultiTool session, not a byte-for-byte terminal capture. The original request body and numerical checks provide independent corroborating material. No new proof was generated or submitted in this turn.

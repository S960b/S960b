# Historical exploit source and reproduction

The **complete successful implementation** is preserved in the participant-provided archive `glacierctf_el-clasico_materials.zip`. The original files, not regenerated guesses, are:

| File in evidence ZIP | Meaning |
| --- | --- |
| `scripts/solver_main.go` | **final working Go prover**: overrides `decomposeScalarG1` and `ratioHint`, loads real keys, locally verifies proof, submits |
| `scripts/search_glv.py` | **final Python parameter search**: lattice reduction, last-window collision, degree-12 polynomial roots |
| `scripts/trace_py.py` | corrected GLV simulator and hint-call trace |
| `scripts/sim_glv_superseded.py` | earlier **incorrect** simulator (sign error), preserved only for debugging history |
| `scripts/probe/probe.go` | real gnark cross-validation against a separate `CheckCircuit` |
| `scripts/tiny/main.go` | diagnostic showing why bare `ccs.Solve` was insufficient |
| `scripts/params.json` | parameters accepted by the server for the captured challenge target |
| `scripts/case1_validation.json` | benign cross-validation case |
| `evidence/submit_request.json` | **actual** successful `POST /submit` body, including the original `proof_hex` |

The organizer's `pk.hex` is ~92.7 MB of ASCII hex **inside** the unmodified handout `assets/el-clasico.tar.gz`. Do not regenerate it: a different proving key would not match the server's original verifying key.

Historical setup in an isolated environment (not run while authoring):

```sh
tar -xzf el-clasico.tar.gz
# Use original challenge Go module with matching go.mod/go.sum.
# Place solver_main.go as solver/main.go, with package main.
go run ./solver -params params.json -dir . -post=false  # local proof, still checks GET /challenge
# Historical validated submission used a live target and POST /submit.
```

The Go solver checks that the target from `GET /challenge` has not changed before proving. An expired or replaced target requires a new slope search. The Python search requires SymPy and used a patched/specific modular root routine in the investigation; consult the archived report for environmental details.

The public [verify_parameters.py](verify_parameters.py) contains a small **offline** check of the saved arithmetic witness without publishing the full proof bytes or flag. The complete working scripts remain in the private evidence archive, where their original names, provenance, and verification logs are retained.

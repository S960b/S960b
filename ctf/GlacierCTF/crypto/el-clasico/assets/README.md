# Original organizer handout

The provided agent bundle contains the **unaltered** `assets/el-clasico.tar.gz`, size **46,348,852 bytes**, SHA-256:

```text
1b050b9a4ef5926b627c17865fdce3e43d75cedf6d69c8a50c853479f33f0721
```

The organizer archive contains `challenge.go`, `go.mod`, `go.sum`, `pk.hex` (~92.7 MB ASCII hex), `vk.hex`, `Dockerfile`, `entrypoint.sh`, `deploy.sh`, `deploy.bat`, `sha256sum`, and **dummy** `flag.txt` (`gctf{fake_flag}`).

Relevant versions:
- gnark `v0.16.4`;
- gnark-crypto `v0.21.1-0.20260913224755-1409fe592052`;
- Go 1.26.8 as used by the recorded agent environment.

The HTTP contract and full circuit definition live in `challenge.go`: `GET /challenge` publishes target `a,b`; `POST /submit` checks `s`, `p_x`, `p_y` and the original BN254 Groth16 proof.

The organizer's packaged `flag.txt` is **not** the live flag. Only the accepted response in `evidence/solve_run.log` contains the real result.

Original files are not duplicated here; read them in the agent bundle or in the handout.

# Evidence and provenance

Source: user-provided `glacierctf_brainrot_materials.zip`, prepared by the local agent on November 26, 2026. The write-up concerns **brainrot**, not the separate `brainrot-revenge`.

## Retained artifacts (private)

- `evidence/brainrot_remote.log` — original successful 21,273-byte session log; includes `[*] Proof of Work passed`, the SWEB boot, the actual `gctf{...}` flag at byte offset 18894, then a kernel panic during teardown. SHA-256: `39a6e0261061155c3f1a7e8bf7c3aaa4e370d0056a7512499fd1d3bf256ec076`.
- `evidence/brainrot_final_payload` — actual 1423-byte attack input, SHA-256 `745f6801de6bfacad34cdc0cd3c97fc1da2eadbc452d51983b341a2da01e941e`.
- `evidence/payload.base64.txt` — the payload in base64.
- `scripts/hc.c` — preserved source of the 8-thread SHA-1 hashcash solver.
- `scripts/brainrot_solve_reconstructed.py` — reconstructed from transcript; **not** the lost original `brainrot_solve.py`.
- `scripts/local_qemu_run_reconstructed.sh` — reconstructed local runner, not the lost original helper.
- `assets/` — original files, hashes in [../assets/README.md](../assets/README.md).

## Independent observations

- Inspecting `bf.c` confirms `printf(outbuf)` inside `flush_out()`.
- Inspecting the original payload confirms a `,.`-based BF echo, `%0256u%0119u`, embedded code, `/usr/flag` and the exact payload hash.
- Inspecting the original remote transcript confirms the full flag was printed by the challenge session.
- The historical conversation also records a successful **synthetic local** `gctf{LOCAL_TEST_SUCCESS}` probe.

The full real flag and the unredacted remote log remain outside the public repository. The participant confirms platform acceptance; a submission screenshot is not available in the retained evidence. The original solver is lost, so reproduction claims are limited to the source and records actually preserved.

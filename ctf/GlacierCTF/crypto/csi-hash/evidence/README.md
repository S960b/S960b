# CSI-HASH verification and evidence

Source: `glacierctf_csi-hash_materials.zip`, prepared by the agent November 27, 2026 from the original Kali run and Git bridge report.

## Directly preserved evidence

- `FLAG.txt`: real full flag returned by the challenge, kept private.
- `evidence/csi_remote.log`: **original raw TCP transcript**, includes the 32-letter challenge and the server's successful second-preimage response with flag.
- `evidence/csi_fixed.log`: full successful solver log, `LLL 32x32; max entry: 21`, all 32 exact quadratic-order relations verified, offline test, submitted preimage, and flag.
- `evidence/get_logs2.out`: completed class group computation, 32 ideal logarithms and `bnfinit` in 3164.0 seconds.
- `scripts/csi_logs.json`: cached logarithms from the same computation.
- `evidence/toy2_rerun.log`: exact-order relation validation on an 8-prime toy model.
- `evidence/handout-checksums-verified.txt`: 7/7 organizer hashes OK.
- `evidence/git-bridge-report.md`: contemporaneous report sent through the bridge.
- `evidence/sage_install.log` / `evidence/pull.log`: unsuccessful environment setup.

## Verifiable final transcript (flag redacted)

```text
[+] invariant factors: [407420844431758343694144149]
[+] LLL 32x32; max entry: 21
[+] all 32 relations verified in the EXACT quadratic order (D=-4p)
[*] TCP connect now
Here is your challenge: PBERSLhMGAMlyzVagLFVVXyYMwdKGIml
[+] submit: M?BOPIeJD>JivwS^dICSSUvVJtaHDFji
Wow, you fund a preimage?
Here, take this: gctf{...}
```

The recovered flag was also accepted by the platform, as confirmed by the participant in the write-up workflow. Historical report was written earlier and did not yet reflect platform submission.

## Precise limits

- The organizer's original dummy `flag.txt` is **not** the remote flag.
- Sage isogeny-hash comparisons were **not run locally** (Sage was unavailable); the exact PARI quadratic-order relation tests and the original live response are retained.
- Screenshots of the platform acceptance screen were not preserved; participant confirmation supplies the status.
- The running CTF endpoint has expired; this write-up does **not** claim a new network test.

The original source, numeric logs, and scripts remain in the evidence ZIP. Public [verify_relation.py](../scripts/verify_relation.py) checks the cached ideal-log relation offline.

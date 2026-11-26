# Evidence and verification

Evidence source: the participant-provided `glacierctf_thin-ice_materials.zip`, assembled from the original 2026-11-21 investigation and files on Kali Linux on 2026-11-26.

## What was directly preserved

- `REPORT.md` — detailed agent chronology, failed branches, routes and counted oracle.
- `FLAG.txt` — real 27-byte recovered value (not published here).
- `evidence/run_flag.log` — authentic successful remote output: PoW success, recovered OIDs, length and the complete 27-byte flag.
- `evidence/flag_state.json` — checkpoint of commit/tree/blob IDs, flag length, completed flag.
- `evidence/flag_state_local.json` — local end-to-end test with the organizer's fake flag.
- `evidence/analysis_monitor.log` — early routing diagnostics.
- `scripts/run_flag.py`, `thin_ice_solver_v2.py`, other scripts — historical implementation.
- `assets/thin_ice.tar.gz` and unchanged handout files.

## Cross-checks

Observed Git objects:

```text
commit: 2c7c30925427d63273b2fb14c7b81c6fe4197074
tree:   6fe60ef3d25dd3e8bccec929a4b44297e80e41a0
blob:   5c5955cf03e044090e9db2f2280fe08acdb1c5e1
blob body length: 27
```

These IDs agree between remote log and checkpoint. Independent offline recomputation of Git object SHA-1 with the recorded flag gives all three exact IDs. The user separately confirmed successful platform submission.

The local `thin_ice_solver_v2.py` test had previously obtained the handout's dummy flag. The original chat at that time did *not* establish remote flag recovery; the later preserved `run_flag.log` does.

## Limits

The raw live server and its temporary 600-second instance are no longer assumed available. No screenshot of the platform's acceptance page is retained, so platform confirmation is attributed to the participant. The published files mask the actual flag and exclude the original private log. No remote requests or renewed exploitation were performed to write this account.

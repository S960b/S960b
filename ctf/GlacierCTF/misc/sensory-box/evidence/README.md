# Evidence and limitations

Source: `glacierctf_sensory_box_materials.zip`, assembled on November 27, 2026 from the original source archive, archived scripts and session notes.

## Preserved

- `REPORT.md`: history of the solution and rejected hypotheses.
- `FLAG.txt`: real 40-byte flag retrieved from the live service (kept private).
- `scripts/leak_via_mtime2.py`: **exact** final exploit.
- `scripts/solve_final_portable.py`: cleaned portable copy, syntax-checked only, not independently re-executed.
- `evidence/final_run_output.txt`: decoded 12 timestamp chunks and recovered flag.
- `evidence/mtime_marker_output.txt`: independent 12-file timestamp marker experiment.
- `evidence/box1_partial_decrypt.txt`: earlier successful one-slice `age` decryption, prefix `gctf{70`.
- `evidence/source_analysis_excerpts.txt`: source locations and Docker sandbox settings.
- `assets/sensory_box.tar.gz`: original handout.

## Evidence boundaries

The final 40-byte value was printed by a script reading the **remote** tar archive; the report describes it as a live flag. There is **no preserved raw tarball, raw TCP transcript, or platform acceptance result**. The final output is a transcription. The agent report explicitly states the flag had not been submitted on the platform.

This repository therefore distinguishes recovered live flag data from a separately confirmed challenge submission. The full flag and temporary age private keys are not copied to public GitHub.

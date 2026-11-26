# Evidence, verification and limitations

The participant-provided `glacierctf_orbital-privacy-screwer_materials.zip` was assembled November 27, 2026 from the previous Kali session and original challenge files.

## Files preserved in the archive

- `REPORT.md` — long investigation chronology, including negative tests and timing analysis.
- `FLAG.txt` — submitted flag recorded by the agent.
- `assets/orbital-privacy-screwer.tar.gz` and original extracted `src/server.py`.
- `scripts/morse4.py` — the **historical successful six-run capture**, hardcoded to read a URL from `/tmp/gigatool/url.txt`.
- `scripts/verify_decode.py` — exact re-encoding check with 67 measured median timings.
- `evidence/05_timing_runs.txt` — six-run measurements and reconstructed Morse.
- `evidence/06_decode_verification.txt` — 67/67 matched classifications.
- `evidence/03_api_behavior_matrix.txt` and `04_negative_tests.txt` — API behavior and rejected approaches.
- `evidence/static_snapshot/` — files from the live instance, including its background image.

## Results

```text
6 successful runs, each 67 intervals
Morse: --. -.-. - ..-. ----- -... .-.. .. ...- - -.. .-. ...-- ... --... -
decoded transmission: GCTF0BLIVTDR3S7T
dots / dashes / separators: 30 / 22 / 15
re-encoding mismatches: none
ideal total: 7936 ms
```

The user **separately confirmed on the platform** that the brace-formatted flag derived from the transmitted text was accepted. The agent's earlier report predates that confirmation and lists platform acceptance as unverified.

## Caveats

The raw SSE capture was not preserved as packet-level evidence; the timing log was transcribed from the console output. No screenshot of the platform's acceptance page exists in this archive. An unrelated longer candidate appeared earlier in the chat but is inconsistent with the measured 67-event Morse signal. The deployed instance's `pct` behavior differed from the archived handout's source (uniform percentage against cumulative-duration percentage), so not every JSON field necessarily came from the same build.

The original challenge's `flag.txt` inside the handout is only a placeholder. No live instance was accessed to prepare this write-up.

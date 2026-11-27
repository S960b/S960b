# Orbital Privacy Screwer (misc) — GlacierCTF 2026

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Author:** Kobalsky
- **Points:** 50
- **Challenge:** “You better hide well, and you do it fast, because otherwise we will find you, and if we have to go to orbit for it, we will.”
- **Historical launcher:** `nc challs.glacierctf.com 13381`
- **Handout:** `orbital-privacy-screwer.tar.gz`
- **Status:** solved; the participant confirmed acceptance of the reconstructed flag by the platform.

## Summary

This is a **timing-based steganography** challenge, not an HTTP injection or file traversal problem. The dashboard's `/api/acquire` endpoint streams 67 Server-Sent Events (`step`) that look like a satellite-tracking progress bar. Their **inter-arrival times** secretly encode Morse code: 67 ms = dot, 133 ms = dash, 200 ms = letter separator.

Six independent measurements and a per-position median decode the 16-character uppercase payload `GCTF0BLIVTDR3S7T`. Interpreting the first four characters as the CTF prefix and wrapping the remaining text in the submitted flag format gives the confirmed accepted `gctf{...}`.

The actual flag is redacted in public repository files according to our usual write-up convention. The observed raw transmission is retained as evidence.

## Files

- [solve.md](solve.md): technical investigation and timing recovery.
- [solve.ru.md](solve.ru.md): Russian write-up.
- [scripts/capture_morse.py](scripts/capture_morse.py): portable version of the original six-run timing capture.
- [scripts/verify_decode.py](scripts/verify_decode.py): **original archived** offline re-encoding check.
- [assets/README.md](assets/README.md): challenge package and relevant source files.
- [evidence/README.md](evidence/README.md): timing measurements and evidence limitations.

No challenge instance was started or contacted while authoring this write-up.

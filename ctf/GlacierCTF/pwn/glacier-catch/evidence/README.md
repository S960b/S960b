# Evidence inventory and limits

Source: participant-provided `glacierctf_glacier_catch_materials.zip`, assembled by the local agent November 26, 2026 from prior discussion and surviving Kali files.

## Direct artifacts preserved in that ZIP

- `REPORT.md`: a detailed timeline, successful/failed variants, offsets, commands, and explicit limitations.
- `FLAG.txt`: the actual 68-byte `gctf{...}` result (not published here).
- `evidence/screenshots/flag128.png`: final 64×32 image containing all flag bytes.
- `evidence/screenshots/flagfull.png`: earlier version with a truncated 64-byte read.
- `evidence/screenshots/glacier-standalone-success.png`: successful marker screen.
- `evidence/logs/glacier-catch*.log`: unsuccessful early attempts.
- `evidence/roms/*.ch8`, `evidence/payloads/*.bin`: ROMs and 536-byte payloads.
- `scripts/working/glacier_catch_clone_renderer_patched.py`: the successful implementation.
- `scripts/working/glacier_catch_standalone_proxyfixed.py`: helper imported by that implementation after naming correction.
- `assets/glacier-catch.tar.gz`: original organizer materials.

## Verified facts

- Renderer-to-Cpu delta on the target was `0x58f0`; local layouts varied.
- Integrity checks passed for 536-byte payload delivery.
- The clone-renderer marker mode produced `ROP_OK!!`.
- A read-dynamic capture yielded the complete flag; read length was raised from 64 to 128 bytes.
- The participant has **separately confirmed** that the recovered flag was accepted by the platform.

## What is NOT independently preserved

- Full raw console transcript of the final successful run.
- Screenshot of the platform acceptance response (participant's confirmation is recorded separately).
- Precise instruction at which the unsuccessful direct renderer-patch versions crashed.
- The exact original challenge statement and author attribution.

No new exploitation or remote test was performed to prepare this repository. Real flags and screenshots containing them remain in the private evidence bundle.

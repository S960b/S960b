# Litter (misc)

- **Platform:** HITCON CTF 2026
- **Category:** Misc (crypto / stego / web-git / ARM64 reverse)
- **Points / Solves:** 197 pts
- **Date:** 2026-10-24
- **Remote:** instancer-hosted web part (masked `<host>`), no other service
- **Flag:** `hitcon{too_many..._pudding}` (masked)
- **Files:** `assets/flag.png.ltr` (ransomed image), `assets/encryptor` (ARM64 ELF from the web part)
- **Description (official):** "My precious pudding image got ransomed :tear: Someone help me."

The challenge is a chain: the video's hidden subtitles reveal the text `Think outside the
box` and a URL to a fake "Litter File Recovery Center"; the web part leaks a legacy
SHA-1 Git archive with an unreachable commit that holds a signed link to a release, which
contains the ARM64 `encryptor`. The saved ELF already carries a rotated-away seed, and the
encryption is AES-192-GCM with a damaged S-box (inputs >=128 map to 0). The final-round
key cancels in the CTR stream, so known PNG bytes (repeated 16 KiB IDAT headers) recover
the whole 24-byte derived key, the GCM tag verifies, and the original picture - with the
flag - is restored.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/recover.py` - full automatic solver (verified: reproduces the PNG, GCM tag and CRCs)
- `scripts/litter_crypto_verified.py` - recovered crypto model (damaged AES-192-GCM)
- `scripts/decode_captions.py` - hidden-message decoder from the YouTube subtitle json3
- `scripts/get_release.sh` - fetched the signed release archive from the web part
- `evidence/VERIFY_RUN.txt` - captured solver output
- `evidence/stage3_*` - the leaked origin/route/signature and smoke log from the old Git commit
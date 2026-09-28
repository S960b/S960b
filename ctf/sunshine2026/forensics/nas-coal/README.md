# NAS coal — VBA macro hiding in a PowerPoint presentation

- **Platform:** SunshineCTF 2026
- **Category:** Forensics
- **Difficulty:** 265 points
- **Date:** 2026
- **Flag:** `sun{yup_...._gem}` (masked)
- **Link:** `<challenge-url>` (challenge instance, closed after the event)
- **Artefact:** `gem_collection.pptm`

"NAS coal" is a macro-enabled PowerPoint deck about somebody's gem collection. The slides are noise with a couple of winks inside them, and the interesting part is not on the slides at all: the deck carries a VBA project (`ppt/vbaProject.bin`) whose `MediaCache.RefreshCache()` sub builds a `powershell.exe -EncodedCommand` line from a long Base64 blob.

Decoding that blob (`Base64 → UTF-16LE`, which is what PowerShell's `-EncodedCommand` expects) yields a small PowerShell snippet. The flag is the `$campaign` string inside it.

Files:

- `solve.md` — the full story, written for beginners: which tools to reach for, what they print, and the dead ends
- `scripts/decode_macro.py` — pulls the Base64 out of the macro and decodes it to readable PowerShell
- `assets/macro_MediaCache.bas` — the decompiled macro, with the flag replaced by a same-length mask
- `assets/decoded_payload.txt` — what that Base64 decodes to (masked excerpt)
- `assets/gem_collection.pptm` — public copy of the deck: slides and layout intact, **macro removed** (the original `vbaProject.bin` contains the live flag and is not published)

Tools that do the work: `unzip`/`7z`, `oletools` (`oleid`, `olevba`, `mraptor`), plus `base64` and `iconv` — no Windows or Office required.

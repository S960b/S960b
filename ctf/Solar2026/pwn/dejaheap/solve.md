# DejaHeap (v10): solving path

## 0. The whole plan

Why this section: so the reader sees the full chain before the details and understands why
each step is needed.

Given: the service accepts a program for its own interpreter and runs it. A file with the flag
sits next to it on the server, and file access goes through a separate broker over shared
memory.

Goal: make the service return the contents of the flag file.

The plan was:

1. Find out what the file is and how to talk to it.
2. Understand what values are made of: numbers, objects, buffers, handles.
3. Understand the heap and the garbage collector.
4. Find the collector bug that leaves a pointer to an object that no longer exists.
5. Make the interpreter copy data through that pointer into shared memory, which the client
   reads.
6. Forge a broker request that opens the flag file.
7. Read the file with a regular request and take the flag.

## 1. What this file is and how to talk to it

Why this step: not to reverse blindly, but to learn the file type and the interface first.

I check the file type with `file`:

```
$ file assets/dejaheap_v10
dejaheap_v10: ELF 64-bit LSB executable, x86-64, version 1 (SYSV), statically linked,
BuildID[sha1]=4ab208..., stripped
```

What each part means. `ELF 64-bit` is the executable format for 64-bit Linux. `statically
linked` means all libraries are inside, there are no separate `.so` files. `stripped` means
function names were removed, so the disassembly will have no hints like `main` or `parse`.
Type `EXEC` (as opposed to `DYN`) means the file is not relocatable: it is always loaded at the
same addresses, so global addresses from the disassembly match addresses in memory. That
matters because later we refer to specific addresses.

The next step is to ask the program itself what it can do. I run it with the help flag:

```
$ ./assets/dejaheap_v10 --help
usage: ... [options] <program.vm>
  --trace            print every executed instruction (author build)
  --dump             disassemble and exit (author build)
  --stats            print engine counters on exit
  --flag=PATH        flag file (default flag.txt)
  --space-kb=N       bytes per semispace (default 64 KiB)
  --steps=N          abort after N instructions
  --test-hooks       enable the author-only test instructions
  -                  read the program from stdin
```

This help gives important facts without guessing. The program takes a `.vm` file, or reads the
program from standard input when `-` is given. The semispace size defaults to 64 KiB, that is
the `--space-kb` option. There is an instruction limit, `--steps`. There is a `--flag` option
that sets the flag file path, `flag.txt` by default. The options `--trace`, `--dump` and
`--stats` are marked as author builds, and I checked that they do not work in this binary:

```
$ ./assets/dejaheap_v10 --dump /tmp/mini.vm
vm: unknown option --dump
```

Now about the language. There are no mnemonic strings in the binary, I checked by searching the
file. So the instruction list and the number of arguments each instruction takes, which is called
its arity, are known from the challenge description rather than from the file. That is why I
checked every instruction by running it. But the behaviour of every instruction can be checked by running it, and that is what
I did constantly. A minimal program:

```
.func main
 const r10 72
 putc r10
 const r10 105
 putc r10
 nl
 exit
.end
```

What is what here. `.func main` opens a function named `main`, the leading dot marks an
interpreter directive rather than an instruction. `const r10 72` puts the number 72 into
register `r10`. `putc r10` prints the character with the code from the register, 72 is the
letter `H`. Then `const r10 105` and `putc r10` print `i`, because 105 is the code of `i`. `nl`
prints a newline. `exit` finishes the program. `.end` closes the function.

I run it and look at the result:

```
$ printf '.func main\n const r10 72\n putc r10\n const r10 105\n putc r10\n nl\n exit\n.end\n' > /tmp/mini.vm
$ ./assets/dejaheap_v10 < /tmp/mini.vm
Hi
```

It printed `Hi` and returned code 0, which I checked with `echo $?`. Two conclusions follow.
First, the input is text in its own language. Second, this is an interpreter: the program does
not execute machine code directly, it parses our text and runs it inside itself. So the target
is the execution environment, meaning the interpreter memory, its objects, and its interaction
with the broker, not machine code.

How strict the parser is, I check with an unknown instruction:

```
$ printf '.func main\n frobnicate r1\n exit\n.end\n' | ./assets/dejaheap_v10
vm: fault
```

The program gives neither the instruction list nor the error position, it just says `vm: fault`
and exits with code 1. So the only reliable way to test hypotheses about instructions is to run
small programs and look at the result. That is what I did next.

## 2. Numbers, objects, buffers, handles and registers

Why this step: the whole attack is replacing object fields, so we need to know exactly how an
object is laid out and how to tell it from a number.

First about registers. A register here is not a CPU register but a named cell inside the
interpreter, like `r0`, `r10`, `r31`. The program refers to them by name and the interpreter
keeps their values in its own memory. A register holds either a number or a reference to an
object.

Why a number and a reference must not be confused. Instructions like `getb`, `setb`, `hread`
expect a reference to an object: they read the object header, take the type and length from it,
and work with the data. If a number is passed instead of a reference, the interpreter treats
the number as an address, and either crashes or reads garbage. So it matters what exactly sits
in a register.

How they are told apart technically. The dispatcher shows that integers are tagged: the value
is stored as `(n << 1) | 1`, so the low bit is always one. References are 16-byte aligned, so
their low bit is zero. Hence the rule: low bit one is a number, zero is a reference.

Now the object layout:

```
+0x00  type (u32)
+0x08  len  (u64)
+0x10  data
```

The first four bytes are the type, the next eight are the length, then the data follows. The
type says what the object is. Four types appear in the code: 1 is a string or a byte array, 2
is a buffer, 3 is a handle, 4 is a coroutine.

A buffer is a type 2 object: it has a length and a data area, you can write into it by index
and read from it. A handle is a type 3 object that describes an open resource: `+0x08` holds
the descriptor, the number by which the operating system or the broker distinguishes open
resources, and `+0x0c` holds the resource type or offset. A coroutine is a type 4 object where
the interpreter keeps the state of a suspended execution, roughly like a separate thread; I did
not use it in this challenge.

That these types and the layout are not invented but checked, I confirmed with the `gdb`
debugger. I run a program that creates a buffer and exits:

```
.func main
 const r1 16
 buf r0 r1
 exit
.end
```

I set a stop on the process termination system call in `gdb` and dump memory:

```
$ gdb -q -batch -x /tmp/g2.txt ./assets/dejaheap_v10
Catchpoint 1 (call to syscall exit_group)
```

Then I dump the mapped region and look for headers. An object was found at `0x7ffff7fd3000`
with type 2 and length 16, followed by 16 bytes of data. Besides it, memory held objects of
type 1 and type 3, that is strings and handles. So I confirmed the header layout and the type
numbering from live process memory, not from text.

## 3. Heap and garbage collector

Why this step: the bug we will use lives in the garbage collector, so its design and its mistake
must be understood.

The heap is two semispaces of 64 KiB each, that default comes from `--help` (the `--space-kb`
option). The allocator is simple and is called a bump allocator: it hands out the next free
chunk upwards and does not look for holes.

The collector is copying. It works like this. There is a set of roots, the places where the walk
starts: registers, the call stack, and global structures the interpreter considers live. The
collector walks references from the roots, finds all reachable objects, copies them into the
second semispace one after another, and declares the first semispace free.

Now the definition we will need. A dangling pointer is a pointer to an object that no longer
exists: the memory at that address is either freed or occupied by another object. If such a
pointer remains somewhere, the program believes the object is alive while something else is
there already.

Where a dangling pointer comes from in a copying collector. If a reference to an object lives in
a place the collector does not treat as a root, then during copying it knows nothing about the
object: it will not copy it and will not update the reference. The object moves or disappears,
and the reference keeps pointing at the old place. That is use-after-free.

How this leads to the flag. If we have a reference to a place where, after collection, another
object ended up, then the command that reads data through this reference reads not what we put
there originally but what is there now, including the header and service fields. And if the
interpreter can copy such an object into shared memory, we get not only a read but also the
ability to replace broker request fields. Then we only have to make the broker open the file.

## 4. How I found the global export structure

Why this step: we need the exact place where the interpreter stores object references outside
the heap, to check whether it is part of the collector roots.

The method was this. First I looked at which global areas the binary has at all. Second I
searched the disassembly for places where object pointers are written into global addresses:
this shows up as store instructions like `mov [rip+offset], rax` near code that works with
objects. That is how the area around `0x50cea8` was found, where the `hexport` instruction
writes.

I checked it not by eye but with an experiment in `gdb`: I compared the state of the globals for
two programs, one without `hexport` and one with it. First the program only creates a buffer:

```
0x50cea0: 0x0
0x50cea8: 0x0
0x50ceb0: 0x0
```

All three words are zero, so the container is empty. Then the program creates a buffer and calls
`hexport`:

```
0x50cea0: 0x00007ffff7fe2290
0x50cea8: 0x00007ffff7fe22b0
0x50ceb0: 0x00007ffff7fe22b0
```

Three pointers appeared, the classic begin, end, capacity triple of a vector, that is a dynamic
array. So this address really holds a container for references to exported objects.

Then I read the collection code and saw where the heap walk starts. This container is not among
the roots. Conclusion: an object registered through `hexport` does not exist for the collector,
and after collection the entry in the container becomes dangling.

What it gives us: this is the entrance into the vulnerability. We register an object ourselves,
we trigger collection ourselves, and afterwards we hold a pointer to a foreign place that we can
prepare with a new buffer.

## 5. How the export becomes a copy

Why this step: a dangling pointer alone does nothing, the interpreter has to act on it by our
command.

`hexport` registers the object in the container and calls the supplied function. When that
function returns, that is on the `ret` instruction of its frame, the interpreter performs a copy
into shared memory:

```
memcpy(ring + off, saved_object, min(saved_object->len, lim))
```

What is what here. `ring` is shared memory, the segment the client maps and reads. `off` is the
cursor, the accumulated offset stored in the global `0x50cf68`. `lim` is the limit: it equals
`0x1c8` if the self-check flag at `0x50ce8c` is non-zero, and `0x100000` otherwise. `hexport`
itself does not let the cursor go past `0x80`, which is visible from a comparison in the
dispatcher near `0x409589`.

How I made sure the copy really happens on `ret` and really reads data through the saved
reference: I built a program where, after `hexport` and the return from the function, bytes
appeared in shared memory that were not there before, and they matched the object data. In the
end the working exploit confirmed the same thing: it reads data that sits in a buffer created
after the export.

What it gives us: a memory read primitive. We feed a dangling pointer, the interpreter copies
whatever is at that address into shared memory, and the client reads it with no restrictions.

## 6. Why a large copy is impossible: the page at R+0x1000

Why this step: I wanted to copy more at once to get more data per run, and had to find out
whether there is a limit.

The first attempt was naive: export a larger object and get a long copy. It crashed with
`SIGSEGV` at `ring + 0x1000`. The reason is visible in the disassembly: the copy is a byte loop,
the area around `0x4c9675`:

```
4c9678: and    edx,0x7
4c967b: je     0x4c9682
4c967d: movs   BYTE PTR [rdi],BYTE PTR [rsi]
```

Then I took the mapping list of my own process (for a local run this is fine, it is my own
process) and saw the layout: `ring = R`, `spaceB = R + 0x2000`, `spaceA = R + 0x12000`. The page
`R + 0x1000` is not mapped at copy time: a temporary 4096-byte broker buffer was there and had
already been removed.

Conclusion: any copy longer than `0x1000 - off` always faults. So the idea of copying a lot at
once is dropped, and all further work happens in a small window.

## 7. Dead buffer and reusing the place

Why this step: the copy window is small, so we need an object with the right length and the
right data exactly at the old address.

First the definition. A dead buffer is an object with no references left in the program. It was
created but nobody uses it, and for the collector it is garbage.

Which reference it loses: the one in a register. In a program this looks like clearing the
register with zero right after creating the buffer, leaving the object unreferenced.

```
buf r0 16
const r0 0        ; the reference to the buffer is erased, the object is garbage
buf r5 16         ; the next buffer, this one we export
hexport r5 worker
```

The `worker` function runs `gc; gc; buf N; ...; ret`, that is two collections, then a new
buffer, then a return. After two collections the new buffer lands where `r5` used to be.

Why the address matches, and which address it is. It is the address stored in the export
container: the address of the `r5` object at the time of `hexport`. The allocator hands out
memory upwards, and collection frees the first semispace entirely. So the first buffer after
collection starts at the same place where the semispace began, and if `r5` occupied that place
before collection, the new object lands exactly there. In practice I had to tune paddings of 56
and 64 bytes so that alignment matched for both the outer and the inner layout.

What it gives us: we now control the contents at the old address. We decide the length and the
bytes that the copy will read.

## 8. Request signature and session key

Why this step: the broker accepts only requests with a valid signature, so without it the
replaced fields give nothing.

How I learned a signature exists. The first forged request was not executed by the broker. Then
I looked at the binary strings with `strings` and found the protocol strings: `HELLO`,
`FLUSH %d`, `DEJA_BROKER`, `%u %u %llu`. They show that there is a handshake and a request with
a field format. Next, in the disassembly I found the function at `0x408130` that computes a
64-bit value over 32 header bytes, and that is exactly what the broker checks.

How the signature is built. The input is 32 bytes assembled from six fields:

```python
data = struct.pack("<IIIIQQ", op, kind, fd, counter, key, length)
```

That is two 32-bit fields, two more 32-bit fields, then two 64-bit ones. The algorithm is the
author's own construction: four rounds over eight input bytes, four 64-bit state words, mixing
with additions, exclusive or and rotations. I recovered it from the function instructions and
ported it to Python.

The key is the last 64-bit field. It arrives in the `HELLO` reply: the client reads the reply,
takes the third field, stores it at `0x50cf58`, and also writes it into shared memory at
`ring + 0x98`, from where the interpreter reads it with `ringpeek`. This is visible both from
the code and from the fact that without reading the key from shared memory the signature does
not match.

The key is random per connection, so the signature cannot be computed in advance on my machine.
The program itself has to compute it in the interpreter language.

Here I needed a way to check that my implementation matches the native one. For that I used an
oracle. An oracle here is a reference implementation: a function that for a given input returns
the correct answer taken from the real code. I took several control values from the native
function on specific inputs, then wrote `scripts/dh_mac_exact.py` that computes the same in
Python, and compared. The script prints the comparison result:

```
op=1, expected=0xc978e7a6ee7de9c7, actual=0xc978e7a6ee7de9c7, match=True
op=2, expected=0xb92f335964a4c1c8, actual=0xb92f335964a4c1c8, match=True
```

A match on the control inputs means the algorithm was ported correctly, so it can be trusted
when generating a program.

How the generator `scripts/dh_runtime_mac_vm.py` computes this signature. The interpreter has no
64-bit arithmetic with the required shifts, so each 64-bit word is represented by a pair of
32-bit registers: the low half and the high half. The generator is ordinary Python code that
prints the program text: it allocates a 256-byte buffer, reads the key from shared memory at
offset `0x98` with `ringpeek`, runs four mixing rounds on register pairs using `add`, `xor`,
`shl`, `shr` and `and`, and then writes the eight result bytes back into the request header at
offset `0x88` with `bset`. So the generator does not compute the signature for us, it produces a
program that computes it at runtime on the server.

What it gives us: the signature is no longer an obstacle. The program computes a valid one for
our replaced fields.

## 9. Shared memory layout and protocol

Why this step: to know exactly which fields to change and where to read the answer from.

I assembled the layout from two sources: the format strings found with `strings` (`FLUSH %d` and
`%u %u %llu` are visible there), and the code that fills these fields.

```
+0x80  op            u32
+0x84  kind/offset   u32
+0x88  MAC           u64
+0x90  fd            u32
+0x94  counter       u32
+0x98  key           u64
+0xa0  length        u64
+0xa8  status        u32
+0xac  broker answer u64
+0xb4  count         u32
+0xc8  data
```

Now the protocol chain step by step, so each requirement is visible.

Handshake. The client sends `HELLO` and receives a reply like `1 <sid> <key>`, where `sid` is the
session identifier and `key` is the signature key. After that the client maps
`/dev/shm/djv8_ring_<sid>`, which is the shared memory.

Open. The `hopen` instruction builds an open request and puts the fields into shared memory. The
`hflush` instruction builds the string `FLUSH %d`, sends it to the broker and performs a single
8-byte `read`. It does not check the reply length, so a short read is accepted too. Then it looks
at `status` at offset `0xa8` and, if it is zero, moves the answer from `0xac` into the handle as
a whole, eight bytes.

Read. The `hread` instruction requires a type 3 handle and a type 2 buffer, and faults if the
descriptor in the handle equals `0xffffffff`. It builds a request with `op=2`, takes `kind` from
the high 32 bits of the open answer, `fd` from the low ones, increments the counter and supplies
the length. The broker puts the answer into shared memory, and the client reads it at `0xc8`.

What it gives us: this layout shows that opening a file only requires replacing two fields, `op`
and `kind`, and that reading can be done with the regular instruction.

## 10. Forged open request

Why this step: to obtain a file descriptor even though we have no regular command to open an
arbitrary file.

The idea relies on copy timing. `hexport` inside a function registers an entry, and the copy
happens on `ret` of that function, that is after `hopen` but before `hflush`. So if prepared
bytes are in the dangling slot by the time `ret` executes, they overwrite the request fields
right before sending. Nested `hexport` calls allow preparing two requests in one run: first the
open, then the read.

A separate problem is the instruction limit. The help mentions `--steps=N`, and on the server it
is set. To measure the budget I used a probe program from the scripts set: it prints `B`, then
every hundred `nop` instructions prints a number. When the budget ends the program faults, and
the last printed number gives a lower bound on the budget. Locally the same mechanism is checked
with `--steps`:

```
$ ./assets/dejaheap_v10 --steps=300 dh_step_budget_probe.vm
B
100
200
vm: fault
```

That is how I learned the server budget covers about 3200 instructions with headroom, and I fit
the program into about 2505 lines.

Result of the first forged open: the broker answered `status=0` and returned `fd=4`. A descriptor
is the number by which the operating system numbers open files; having it, we can refer to an
open file without knowing its path. That is exactly what is needed to reach the flag: the file is
open, all that remains is to read it.

What it gives us: we hold the number of an open file and can ask the broker to read data from it.

## 11. Regular read and the flag

Why this step: to read the file through the descriptor we obtained, without forging a second
request.

After the open, `hflush` moved the answer into the handle. Then the regular `hread` built the
request `op=2`, `kind=0`, `fd=4`, counter 1 and a correct signature by itself, because it has the
key and takes the fields from the handle. The broker returned the data, and `ringpeek` read it
from shared memory at offset `0xc8`.

The markers in the output are our own labels, not program messages. Before each stage the program
has a pair of instructions like `const r10 65` and `putc r10`, where 65 is the code of `A`. I put
them there myself, to tell from the output how far execution got if something crashes. That also
explains the letter order: `W` and `G` sit inside the `worker` function, so they print before `C`
and `D`.

```
A B W G M C D F 0 0 0 0 4 0 0 0 R S SOLARCTF{31a...fe4}
```

The eight numbers `0 0 0 0 4 0 0 0` are the byte values printed from the broker answer: status and
descriptor. `R` and `S` mark the transition to reading and the success of the read, after which
the flag is printed.

What it gives us: this is the end of the chain, from the dangling pointer to the actual flag
bytes.

The ready bytecode is in `scripts/win_open0_full.vm`, submission is done by
`scripts/submit_remote.py`:

```
DH_HOST=<host> DH_PORT=<port> python3 scripts/submit_remote.py scripts/win_open0_full.vm
```

To regenerate the program for another `kind` value:

```
python3 scripts/dh_op1_probe_gen.py --kind 0 -o /tmp/open0.vm
```

## 12. What did not work, and why

- `kind=8` in the forged open. No answer arrived, the read hung for about thirty seconds until the
  wrapper timeout. Such a resource seems to behave like a pipe rather than a file. With `kind=0` a
  readable descriptor 4 appeared at once.
- Brute forcing descriptors. Pointless: readability was determined by the `kind` field at open
  time, not by the descriptor number.
- A copy longer than the window. It always faulted on the unmapped page `R + 0x1000`.
- Acknowledgement desync. The client reads the acknowledgement with a single 8-byte `read` and
  does not check the length. In theory this is another bug, but it could not be reproduced
  reliably on the real broker and was not used.
- Debugging under a debugger. The binary strings include `/proc/self/status` and `TracerPid`, so
  the program checks whether it is being debugged. When I ran it under `gdb`, the self-check flag
  at `0x50ce8c` became one, the copy limit dropped to `0x1c8`, and the vulnerability was masked.
  Part of the checks had to be done without a debugger, by markers.

## 13. In one paragraph

Object export is not part of the collector roots, so a registered object is left dangling after
collection and its place is taken by a new buffer. On return from the function owning the entry
the interpreter copies that buffer into shared memory, which gives controlled replacement of
request fields. The signature is computed with a random session key, so the program computes it
at runtime. Having forged an open with `kind=0`, I got descriptor 4, then a regular read read the
file, and `ringpeek` collected the flag.

## 14. Lessons

- A copying collector is only as safe as its root list is complete. Any global container of
  pointers added outside the roots gives use-after-free.
- It pays to build a memory model early: tags, object types, heap layout. It pays off when
  predicting what will occupy the dangling slot.
- An instruction limit is measured with a probe program and `--steps`, not estimated by eye.
- A debugger check inside the program changes behaviour, and it is worth finding early, otherwise
  part of the observations will be false.

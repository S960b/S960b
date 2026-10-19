# DejaHeap (v10): solving path

## 0. The whole plan

Why this section: so the reader sees the chain before the details.

Given: the service accepts a program and runs it in its own interpreter, and a file with the
flag sits next to it on the server.

Goal: make the service return the contents of that file.

My plan was:

1. Understand what kind of program this is and how to talk to it.
2. Recover the interpreter memory model: values, objects, heap, garbage collector.
3. Find the collector bug that leaves a dangling pointer.
4. Turn that pointer into a memory read that lands in shared memory.
5. Forge a broker request that opens the file.
6. Read the file with a regular request and take the flag.

## 1. First run: what this program is

Why this step: not to reverse blindly, but to learn the interface and the file type first.

First I look at the file itself:

```
$ file assets/dejaheap_v10
dejaheap_v10: ELF 64-bit LSB executable, x86-64, version 1 (SYSV), statically linked,
BuildID[sha1]=4ab208..., stripped
```

It is a static stripped binary of type `EXEC`, so not PIE. That means global addresses at run
time match the disassembly addresses, and this can be used as a foothold.

Next I check what it does on a minimal input. I write the smallest program and run it:

```
$ printf '.func main\n const r10 72\n putc r10\n const r10 105\n putc r10\n nl\n exit\n.end\n' > /tmp/mini.vm
$ ./assets/dejaheap_v10 < /tmp/mini.vm
Hi
```

It printed `Hi` and exited with code 0. Two facts follow. First, the input is text in its own
language with statements like `const`, `putc`, `nl`, `exit`. Second, this is an interpreter,
not an ordinary program, so the target is the execution environment rather than machine code.

It is worth checking how strict the parser is: I feed it an unknown instruction.

```
$ printf '.func main\n frobnicate r1\n exit\n.end\n' | ./assets/dejaheap_v10
vm: fault
```

The error carries no hints and does not list valid words. So the mnemonic list is not exposed:
it is recovered from the dispatcher in the disassembly, and each instruction is then confirmed
by running a small program. That is the method I used: every statement about instruction
behaviour below is backed by a run, not only by reading code.

## 2. Value model

Why this step: we work with buffers and handles, so we must know what sits in a register and
not mix a number with a pointer.

The dispatcher shows that integers are tagged: the value is stored as `(n << 1) | 1`, so the
low bit is always one. An object reference is 16-byte aligned, so its low bit is zero. Hence a
simple rule: low bit one means a number, otherwise a reference. This is easy to check:
arithmetic on literals behaves normally, and `print` prints the number, not an address.

The object layout:

```
+0x00  type (u32)
+0x08  len  (u64)
+0x10  data
```

Types seen in the code: 1 is a string or bytes, 2 is a buffer, 3 is a handle, 4 is a coroutine.
The handle is a separate structure: `+0x08` holds the descriptor, `+0x0c` holds the type or
offset.

Why this matters: a type 2 object is our working buffer, and a type 3 object is what requests
to the broker go through. Almost the whole attack is replacing fields of exactly these.

## 3. Memory and the garbage collector

Why this step: a dangling pointer is born in the collector, so its design is the centre of the
challenge.

The heap is two 64 KiB semispaces. The allocator is a bump allocator, it simply hands out the
next chunk upwards. The collector is copying. In plain words it works like this: the collector
walks live objects starting from the roots, copies them into the second semispace and declares
the first one free. Anything not reached from the roots disappears.

This immediately tells where to look for the bug: if a pointer to an object lives somewhere the
collector does not start from, then for the collector the object is dead. It will not be copied
or updated, and the pointer keeps pointing at the old place. That is use-after-free.

I look for such places. The disassembly has a global structure at `0x50cea8` where `hexport`
stores a reference to the exported object. I check that these globals are actually used:

```
$ objdump -d -M intel assets/dejaheap_v10 | grep -cE '50cea8|50cf68|50ce8c|50cf58'
16
```

Sixteen references to four globals: `0x50cea8` the export vector, `0x50cf68` the cursor into
shared memory, `0x50ce8c` the self-check flag, `0x50cf58` the session key. The code shows the
heap walk starts from other roots, and the export vector is not among them.

Why this step: an object registered through `hexport` is invisible to the collector, so after
collection the vector entry becomes dangling.

## 4. How the export becomes a copy

Why this step: a dangling pointer alone is useless; the program has to do something with it on
our command.

`hexport` both registers the object and calls the supplied function. When the function
returns, that is on `ret` of its frame, a copy happens:

```
memcpy(ring + off, saved_object, min(saved_object->len, lim))
```

Here `ring` is the shared memory the service gives the client, `off` is the accumulated cursor
from the global `0x50cf68`, and `lim` is the limit from the self-check flag: `0x1c8` if
`*(int*)0x50ce8c` is non-zero, otherwise `0x100000`.

`hexport` itself does not let the cursor go past `0x80`, which is visible from the comparison
at `0x409589`. After the copy the cursor grows by the number of bytes actually copied.

Why this step: we now have a memory read primitive. Feed a dangling pointer into the vector,
the interpreter copies the old object contents into shared memory, and the client reads it
directly.

## 5. First dead end: the page at R+0x1000

Why this step: to find out how long a copy can be before building anything else.

The first naive attempt was to export a larger object and get a longer copy. It died with
`SIGSEGV` at `ring + 0x1000` inside `rep movsq` at `0x4c9675`. I confirm that this is a byte
copy loop:

```
$ objdump -d -M intel --start-address=0x4c9660 --stop-address=0x4c9685 assets/dejaheap_v10
4c9678: and    edx,0x7
4c967b: je     0x4c9682
4c967d: movs   BYTE PTR [rdi],BYTE PTR [rsi]
```

Then I take the mapping dump of my own process and see the layout: `ring = R`,
`spaceB = R + 0x2000`, `spaceA = R + 0x12000`. The page `R + 0x1000` is not mapped at copy
time: it is a temporary 4096-byte broker buffer that is unmapped on `ret`.

Conclusion: any copy longer than `0x1000 - off` always faults, and writing from shared memory
back into a semispace is impossible. This is a dead end, and I recorded it so as not to return
to growing the length.

## 6. Reusing the stale slot

Why this step: the copy window is small, so the old address must point at an object with the
right length and the right data.

The trick: a dead buffer is created in front of the exported object and immediately loses its
reference.

```
buf r0 16
const r0 0        ; reference dropped, object becomes garbage
buf r5 16         ; the exported object
hexport r5 worker
```

The worker runs `gc; gc; buf N; ...; ret`. After two collections the new buffer lands at the
address of the former `r5`, and the copy on `ret` reads its length and data.

Why the address matches: the allocator is a bump allocator and collection frees the first
semispace entirely, so the next buffer starts at the same place. By tuning paddings of 56 and
64 bytes I got a stable match for both the outer and the inner layout.

Why this step: use-after-free is now controlled. We decide what sits in the dangling slot at
copy time.

## 7. The signature and the session key

Why this step: the broker verifies requests by signature, and without a valid one the forged
fields are simply dropped.

I found the function at `0x408130` and read it. It is a custom 64-bit hash, not SipHash, with
its own constants. The input is 32 bytes:

```python
data = struct.pack("<IIIIQQ", op, kind, fd, counter, key, length)
```

The key comes from the third field of the `HELLO` reply. The client keeps it at `0x50cf58` and
writes it to `ring + 0x98`, where the interpreter reads it with `ringpeek`.

The key is random per connection, so the signature cannot be computed offline. The program has
to compute it itself in our language. I wrote the generator `scripts/dh_runtime_mac_vm.py`,
which implements this hash on two 32-bit registers, and checked the result against native code
with a separate oracle, `scripts/dh_mac_exact.py`.

Why this step: without a correct signature no amount of field corruption has any effect.

## 8. Shared memory layout and protocol

Why this step: to know exactly which fields to replace and where to read the answer from.

I reconstructed the layout from the code and from experiments:

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

The protocol starts with `HELLO`, the reply looks like `1 <sid> <key>`, and then the client maps
`/dev/shm/djv8_ring_<sid>`.

`hflush` builds the string `FLUSH %d`, sends it and performs a single 8-byte `read`, without
checking the length. It then looks at `status` at `ring + 0xa8` and moves the answer from
`ring + 0xac` into the handle as a whole, 8 bytes.

`hread` requires a type 3 handle and a type 2 buffer, faults if the descriptor equals
`0xffffffff`, and builds the request `op=2`, `kind` from the high 32 bits of the open answer,
`fd` from the low ones, `counter` incremented, and `length`. The answer is read from
`ring + 0xc8`.

Why this step: this table shows that opening a file only requires replacing the `op` and `kind`
fields, after which regular reading can be used.

## 9. Forged open request

Why this step: to obtain a file descriptor even though we have no legitimate command to open an
arbitrary file.

The idea relies on the copy timing. `hexport` inside a function registers an entry, and the copy
happens on `ret` of that function, that is after `hopen` but before `hflush`. So by placing
prepared bytes in the dangling slot I overwrite the request fields right before they are sent.
Nested `hexport` calls allow preparing two requests in one run.

The program had to fit in about 2505 lines to stay within the server step limit: the calibrator
showed headroom up to roughly 3200.

First result: a forged request with `op=1` and `kind=0` went through, the broker answered
`status=0` and returned `fd=4` in the answer field.

Why this step: we now have a working descriptor for a regular file.

## 10. Regular read and the flag

Why this step: to read the file through the descriptor we obtained, without forging a second
request.

After the open, `hflush` moved the answer into the handle, and the regular `hread` then built
`op=2`, `kind=0`, `fd=4`, `counter=1` and a correct signature by itself. The broker returned the
data, and `ringpeek` read `ring + 0xc8`.

Markers of the winning run in the output:

```
A B W G M C D F 0 0 0 0 4 0 0 0 R S SOLARCTF{...}
```

The marker order follows the program stages, `0 0 0 0 4` is the status and descriptor from the
broker answer, `R S` is the transition to reading, after which the flag is printed.

Why this step: this is the end of the chain, from the dangling pointer to the actual flag bytes.

The ready bytecode is in `scripts/win_open0_full.vm`, submission is done by
`scripts/submit_remote.py`:

```
DH_HOST=<host> DH_PORT=<port> python3 scripts/submit_remote.py scripts/win_open0_full.vm
```

To regenerate the program for another `kind` value:

```
python3 scripts/dh_op1_probe_gen.py --kind 0 -o /tmp/open0.vm
```

## 11. What did not work, and why

- `kind=8` in the forged open request. No answer arrived: the read blocked for about 30 seconds
  until the wrapper timeout, with no acknowledgement. It behaves like a pipe rather than a file.
  With `kind=0` a readable `fd=4` appeared at once.
- Brute forcing descriptors. Pointless: the readable channel was determined by the `kind` field
  at open time, not by the descriptor number.
- A copy longer than the window. It always faulted on the unmapped page `R + 0x1000`, so the
  idea of writing from shared memory into a semispace was dropped.
- Acknowledgement desync. The client reads the acknowledgement with a single 8-byte `read` and
  does not check the length, so a short read is accepted. In theory this is another hole, but it
  could not be reproduced reliably on the real broker and was not used.
- Debugging under ptrace. The self-check flag at `0x50ce8c` becomes one under a debugger, the
  copy limit drops to `0x1c8`, and the vulnerability is masked. Part of the checks had to be done
  without a debugger, by markers in the output.

## 12. In one paragraph

Object export is not part of the collector roots, so a registered object is left dangling after
collection and its old place is taken by a new buffer. On `ret` of the frame owning the entry the
interpreter copies that buffer into shared memory, which gives controlled replacement of request
fields. The signature key is random per connection, so the signature is computed at runtime
inside the program. Having forged an open request with `kind=0`, I got `fd=4`, then a regular
read read the file, and `ringpeek` collected the flag.

## 13. Lessons

- A copying collector is only as safe as its root list is complete. Any global container of
  pointers added outside the roots gives use-after-free.
- It pays to build a memory model early: tags, object types, heap layout. It pays off when
  predicting what will occupy the dangling slot.
- A step limit is solved by optimising the generator, not by cutting logic.
- A self-check flag that changes behaviour under a debugger is worth finding early, otherwise
  part of the observations will be false.

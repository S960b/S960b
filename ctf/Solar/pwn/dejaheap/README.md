# DejaHeap (pwn)

The service takes a program for its own interpreter and runs it. A file with the flag sits
next to the interpreter on the server, and file access goes through a separate broker over
shared memory: the client maps a segment, writes a request into it and reads the answer back.

The challenge ships a single executable, `dejaheap_v10`: static, stripped, non-PIE, Linux
x86-64. It implements a virtual machine with its own text language, a garbage collector and
instructions for talking to the broker.

Goal: make the service return the contents of the flag file.

Key facts:

- The heap is two 64 KiB semispaces, the collector is copying, the allocator is a bump
  allocator.
- The export vector at `0x50cea8` is not part of the collector roots, so a registered object
  is left dangling after collection.
- The copy into shared memory happens on `ret` of the frame that owns the entry.
- The request signature is computed with a key that arrives in the `HELLO` reply and is random
  per connection.

Files:

- `assets/dejaheap_v10` - the interpreter as shipped
- `scripts/dh_runtime_mac_vm.py` - generator of a program that computes the signature at runtime
- `scripts/dh_op1_probe_gen.py` - generator of a program with a forged open request
- `scripts/dh_mac_exact.py` - exact signature implementation used to check against native code
- `scripts/win_open0_full.vm` - ready bytecode of the winning run
- `scripts/submit_remote.py` - submits a program to the service

The full solving path is in `solve.md`.

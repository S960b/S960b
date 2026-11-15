# Matryoshka: original session checkpoints

Competition session of 13 November 2026:

- **20:04:** `strace` showed `memfd_create("stage2", MFD_CLOEXEC)` followed by a `write` of **154,328 bytes** beginning with the ELF signature. The nested `stage2.bin` was reconstructed from the captured write stream.
- **20:05:** the stage printed `Enter password:` and rejected non-hexadecimal input. Its `.rodata` contained `Password correct!`, `Wrong Password!` and `Here is your flag:`.
- **20:06:** binary Gaussian elimination recovered `c667c58c3b02d6c91b8def4fdd1bc5f3`, satisfying all four CRC32 checks.
- **20:06:** XOR of 36 bytes from `stage2.bin` offset `0x1b560` with the recovered 16-byte password yielded the full flag.
- **20:06:** running `echo c667c58c3b02d6c91b8def4fdd1bc5f3 | ./stage2.bin` printed:

```text
Enter password: Password correct!
Here is your flag: PP{th3r3_1s_4_b1n4r…4ry?!}
```

- **20:06:** platform submission returned `HTTP 200 {"status":1}`.

The later reproduction package includes the original 168,872-byte outer ELF, a small tracing shim and `out1.txt` with the same successful output. Manual XOR is an inspection technique, not an additional step required when the correct password is supplied to the executable.

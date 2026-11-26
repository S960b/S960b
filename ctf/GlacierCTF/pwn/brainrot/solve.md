# brainrot — format-string exploitation inside SWEB

[Русская версия](solve.ru.md)

## 1. Target: Brainfuck in an educational OS

This is the first **brainrot** challenge, not **brainrot-revenge**. The organizer provides the interpreter source `bf.c`, its compiled `bf.sweb`, a compressed SWEB disk image, `exe2minixfs`, and `run.sh`. The guest is x86-64 SWEB running in QEMU with 8 MiB RAM; `/usr` uses MinixFS and `/` is ramfs. The local launcher uses `-cpu qemu64,+smep`.

The remote endpoint requests a SHA-1 hashcash proof of work (the preserved solver handles `-mb29`), then accepts **one base64-encoded file**. Within the decoded file, line 1 is the Brainfuck program (up to 4096 bytes) and line 2 is its fake stdin (up to 16384 bytes). The guest stores the file as `/usr/prog` and starts `/usr/shell.sweb`.

The original `bf.c` uses static buffers: `code[4097]`, `input[16384]`, `tape[30000]`, `outbuf[1024]`, and `filebuf[0x18000]`. The trace places `input` at `0x08025a80`.

## 2. The bug

The vulnerable function is extremely short:

```c
static void flush_out(void)
{
  if (out_pos == 0)
    return;
  outbuf[out_pos] = '\0';
  printf(outbuf);
  out_pos = 0;
}
```

The first argument to `printf` comes directly from Brainfuck output. Because there are no corresponding variadic arguments, percent directives are interpreted as a *format string*, not literal text. This is CWE-134.

A safe implementation would call `printf("%s", outbuf)` or `fwrite(outbuf, 1, out_pos, stdout)`.

The interpreter emits characters through `.`, loads bytes through `,`, and flushes on a newline, after 1023 output bytes, or at the end of the program. We do not need pointer movement or arithmetic: the BF language itself provides a direct input-to-`printf` route.

## 3. Delivering the format string

The original payload contains 1423 bytes. The BF line is constructed as:

```python
code = (b",." * 512 + b",").ljust(1280, b"Z")
```

Each `,.` reads one byte from fake stdin into the current tape cell and immediately emits it. The remaining `,` consumes one more byte, and the `Z` padding is ignored by the BF interpreter. The interpreter outputs the attacker-supplied input until the first NUL byte; `flush_out` then passes that content directly to `printf`.

The fake stdin begins with `%0256u%0119u`, contains native machine code and further `%c`/`%s` directives, and ends with a NUL followed by `/usr/flag\0`. The target path is deliberately placed after NUL: it remains present in static `input[]` while `printf` sees a terminated format string.

The preserved final payload has these SHA-256-identified properties:

```text
total length             1423 bytes
Brainfuck line           1280 bytes
fake stdin line           141 bytes
first format directives  %0256u%0119u
embedded shellcode       x86-style SWEB int 0x80 sequence
path                     /usr/flag
SHA-256                  745f6801de6bfacad34cdc0cd3c97fc1da2eadbc452d51983b341a2da01e941e
```

## 4. Stack corruption and control-flow transfer

The historical exploit relies on the exact SWEB `printf` implementation and its stack-frame layout. The width directives `%0256u%0119u` expand output substantially beyond what the programmer likely expected; the following format directives consume unintended arguments. Literal binary bytes in the format string are positioned to overwrite/use saved stack control data.

The original payload includes the address-like byte sequences:

```text
47 ff ff ff ff 7f
7a ff ff ff ff 7f
```

They correspond to pointers in the high-address SWEB stack area (the historical trace discusses a pivot to `0x7fffffffff7a`). The payload also has a NOP sled and a 56-byte slot for the code that performs file I/O.

This is **not** a `%n` overwrite: the recovered technique exploits the formatter's widths and stack behavior. We do not claim a modern glibc exploit is equivalent to the challenge's custom runtime. The surviving materials and remote flag prove successful control-flow transfer, but a complete instruction-by-instruction trace of the corrupted `printf` stack is not retained.

## 5. SWEB shellcode and ABI

The final payload uses `int 0x80`. Its calls follow the SWEB userspace convention visible in the working code, with the syscall number in EAX and arguments in EBX/ECX/EDX (the original report contains a shorthand that accidentally repeats RBX). The recovered shellcode follows this sequence:

```text
open("/usr/flag", O_RDONLY)               eax=5, ebx=path, ecx=1
read(fd, 0x08025a80, 0x7f)                eax=3, ebx=fd, ecx=buffer, edx=127
write(1, 0x08025a80, 0x7f)                eax=4, ebx=1, ecx=buffer, edx=127
exit(0)                                   eax=1, ebx=0
```

After `open`, `mov ebx,eax` forwards the actual file descriptor instead of assuming a fixed value. The buffer is the global `input[]`, already mapped in the process. The shellcode prints a fixed 127 bytes and therefore may include padding beyond the flag.

A local test earlier in the conversation returned the synthetic `gctf{LOCAL_TEST_SUCCESS}` from `/tmp/flag`. That is evidence that the ORW technique worked locally, not the competition flag.

## 6. Proof of work and remote result

The retained `hc.c` is a multithreaded SHA-1 hashcash generator. The session flow was:

```text
connect to challs.glacierctf.com:13370
receive hashcash parameters / unique resource
generate a hashcash token (29 leading zero bits)
send the token
receive "program> "
send base64(<two-line payload>)
receive the SWEB debug console and the flag
```

The preserved `evidence/brainrot_remote.log` begins:

```text
[*] Proof of Work passed
=== brainrot ===
Send your Brainfuck program as ONE base64 line:
  line 1 = Brainfuck code, line 2 = fake stdin (base64 the whole file).
program> [*] booting SWEB (debug console follows; grep for the flag)...
```

The same **21,273-byte** remote log includes `gctf{...}` (masked here) at byte offset 18894. After the flag, the guest later reports an assertion in `File::~File` about file descriptors during filesystem teardown, followed by a kernel panic. The flag is already present in the output; a clean guest shutdown is not necessary for CTF success.

The historical session records flag recovery. A separate screenshot of platform acceptance was not saved; this is deliberately not conflated with the observed remote stdout.

## 7. Artifacts and reproduction boundaries

The private `glacierctf_brainrot_materials.zip` contains the organizer's handout files, the exact 1423-byte payload, a 21,273-byte successful remote log, `hc.c`, and a reconstructed Python solver. The original solver and QEMU helper script were lost; their recreated versions are labeled `reconstructed`.

The published `scripts/brainrot_solve_reconstructed.py` is intentionally **offline-only**: it recreates the input and can write a payload file without contacting an expired competition endpoint. It should not be misrepresented as an independently rerun remote exploit. The private archive retains the actual submission bytes and command logs.

With the original handout, the intended historical local workflow is: unpack `brainrot.vmdk.gz`, copy the disk, call `exe2minixfs <disk> 10321920 <payload> prog`, and boot with `run.sh`/`qemu-system-x86_64`. The report records these commands; this write-up did not rerun the binary.

## 8. Takeaways

- An educational OS is not a security boundary against bugs in applications it runs.
- Safe Brainfuck tape bounds do not help if the output sink is a vulnerable formatter.
- Controlled program output plus `printf(user_text)` exposes formatter-specific stack behavior.
- Keep the userland syscall ABI, stack layout, and actual environment separate from Linux assumptions.
- Preserve successful remote logs and original payload hashes; reconstruct lost tools only with explicit labels.

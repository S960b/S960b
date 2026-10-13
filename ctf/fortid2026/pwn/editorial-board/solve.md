# Editorial Board - writeup (i386 printable-ASCII shellcode hidden in valid English prose)

## Getting to know the task

The handout is two files:

- `editorial_board` - a 32-bit ELF, stripped (no symbols)
- `validate.py` - the validator that is spawned by the binary

The service asks for one English "manuscript" (<=900 bytes), validates it, and if the text is accepted, copies it into an RWX `mmap` region and then into a stack buffer with an unchecked `rep movsb`. The program then returns - and returns into our text.

The twist that makes this challenge special: the payload must be **valid English prose** that the grader accepts AND a working exploit. Only `[A-Za-z]` and ` .,;:'-?!` are allowed in the text (the binary rejects anything else). So the exploit is written as **self-modifying printable-ASCII i386 shellcode embedded in an otherwise grammatical English paragraph**.

## How I read the binary and the validator (which tools)

- `file editorial_board` - 32-bit ELF, i386, stripped. So no symbol names; I worked from disassembly.
- `objdump -d -M intel editorial_board` - the disassembly. I looked for: where the input is read, what happens after validation, and where the text ends up in memory.
- `strings editorial_board` - the prompts ("Submit your manuscript:", "The board is reviewing your manuscript...") that tell which syscall does what.
- `validate.py` (given) - the grading rules, so I knew exactly what the text has to satisfy before the exploit even runs.
- `qemu-i386` + 32-bit libc - to run the ELF locally (the binary is i386 on an x86-64 box).

## The vulnerability (from the disassembly)

- `main` @ `0x804984a`: `mmap(0x656c6000, 0x10000, PROT_READ|WRITE|EXEC, MAP_FIXED|ANON, -1, 0)` - a fixed RWX region (no ASLR for the mapping address).
- `read(0, [ebp-0x399], 0x384)` @ `0x804990b` - reads **at most 900** bytes of our manuscript.
- The validator is spawned via `fork` + `execl("python3", "./validate.py", "--quiet", "--stdin")`.
- On ACCEPT: `memcpy(RWX, buf, n)`, then a vulnerable function @ `0x804979e`.
- The vulnerable function does `sub esp,0x259` and then `rep movsb` into `[ebp-0x259]` - **no bounds check**. The offset from the start of the copied text to the saved EIP (return address) is `0x25d` = **605** bytes.

So: if our manuscript bytes 605..608 spell a valid address, after the function returns, EIP jumps there. The RWX region is at `0x656c6000`, and the same manuscript is mapped there, so we set the return address to point back into our own text. The bytes `"able"` = `61 62 6c 65` little-endian = `0x656c6261` = `0x656c6000 + 0x261` (the manuscript offset 609). So `message[605:609] = b"able"` makes execution start at offset 609 of the RWX region - the beginning of our shellcode.

Another important detail from the disassembly: `setvbuf` sets stdout/stdin **unbuffered**, so `write(1, ...)` from shellcode is visible immediately (no flush needed).

## The constraint: every allowed character is an x86 opcode

The allowed set `[A-Za-z]` + ` .,;:'-?!` was chosen because in x86 every one of those bytes is a valid opcode. I built a table from the opcode map (checking each byte against `objdump` disassembly of a test buffer):

| Char | Byte | Instruction |
|---|---|---|
| `A` | 0x41 | `inc ecx` |
| `B` | 0x42 | `inc edx` |
| `R` | 0x52 | `push edx` |
| `X` | 0x58 | `pop eax` |
| `Z` | 0x5a | `pop edx` |
| `P` | 0x50 | `push eax` |
| `G` | 0x47 | `inc edi` |
| `.` | 0x2e | CS segment prefix |
| `,` | 0x2c | `sub al, imm8` |
| `:` | 0x3a | `cmp ...` |
| `-` | 0x2d | `sub eax, imm32` |
| `!` | 0x21 | `and ...` |

The problem: the shellcode needs bytes that are NOT in the alphabet - e.g. `0x5b` (`pop ebx`) and `0xcd 0x80` (`int 0x80`). You cannot type them into the manuscript. The solution is **self-modification**: the shellcode patches its own bytes at runtime.

**How the self-modification works:** the prologue zeroes `AL` (`sub al, imm` / `xor eax,eax`), then uses `AND BYTE PTR [ECX+disp], AL` (bytes `0x00 41 dd`, written via `.`/`A` + a displacement) to zero a target opcode byte in the mapped manuscript itself. With AL=0 the AND zeroes the byte; subsequent `SUB AL, imm8` sequences build up AL to the wanted value, and a final `ADD BYTE PTR [ECX+disp], AL` writes the desired opcode. This way forbidden bytes like `0x5b` and `0xcd 0x80` are synthesized, not typed.

## The critical native-x86 bug (why it worked under QEMU but not on the server)

The first "working" chain ran fine under `qemu-i386` but **silently failed on the real server**: the manuscript was accepted, but no output appeared.

Root cause (from disassembling the patched bytes and checking the byte at the write site): one of the self-modifying stores decoded as

```text
2e 00 41 xx    add byte ptr cs:[ecx+xx], al
```

i.e. an **ADD through the CS segment prefix**. On native protected-mode x86 a `CS:` memory write raises `#GP` (general protection fault) regardless of the page being RWX. QEMU user-mode does not enforce this. So the exploit "worked" locally (under QEMU) and died remotely (native x86).

**Fix:** drop the CS prefix from that store - emit the plain

```text
00 41 62       add byte ptr [ecx+0x62], al
```

and compensate the removed prefix byte by two extra `inc ecx` so the store still targets the same byte. Confirmed remotely: the server printed the marker, proving the first stage executed natively.

> Lesson: never trust `qemu-i386` for the correctness of segment-prefix / faulting instructions. Validate on the real target.

## The editorial (LanguageTool) countermeasures - the hardest part

The grader is genuinely the hardest part. Every change to the machine bytes perturbs the token stream and produces `SENTENCE_WHITESPACE` / `COMMA_PARENTHESIS_WHITESPACE` / `MORFOLOGIK_RULE_EN_US` findings that drop the score below 88.

The steps that finally passed:

1. **Remove the comma in the prologue.** The original ` AG AO AW,ax:` caused a permanent `COMMA_PARENTHESIS_WHITESPACE` finding. Replaced with ` AJ AR AZ-<Word>:` where `-<Word>` is `sub eax, imm32` and the trailing char is a taken conditional jump.
2. **Short, dot-separated tokens.** A block like `.RABPAX` forms the "word" `RABPAX`, which LanguageTool flags as `MORFOLOGIK_RULE_EN_US`. Replacing it with **`.R.A.BA`** (dots between ops - short tokens) removes the spelling finding.
3. **Reduce repetition** by swapping repeated content words for synonyms of identical length (so the offset 605 is preserved): `soft - thin`, `team - crew`, `brief - short`, `can - may/did`.

The final accepted first-stage manuscript (899 B):
- prologue ` AJ AR AZ-Sugar:`
- starter `Sugar`, subword `rain`, the token block `.R.A.BA`
- score = **89.45**, spelling 0, issues 4, repetition 0.01 - **ACCEPT** (verified by the original `validate.py`)

The machine invariants held: `pop ebx` synthesized at `+100`, `int 0x80` at `+115`, `read(0, EIP+6, 115)` for the second stage.

## The final exploit structure (two stages)

```text
offset   0 .. 608   English prose (offsets 605-608 = "able" - EIP 0x656c6261)
offset 609 .. 725   117-byte self-modifying ASCII shellcode  (stage 1)
offset 726 .. 898   English tail (padding + grammar filler)
```

**Stage 1** (in the RWX region) synthesizes:

```text
eax = 3          ; sys_read
ebx = 0          ; stdin
ecx = EIP+6      ; destination inside the RWX region
edx = 115        ; count
int 0x80
```

i.e. `read(0, EIP+6, 115)` - pulls a raw binary **second stage** from the socket into executable memory.

**Stage 2** (115 raw bytes, built as `stage2 = core + NOPs + EB 8F`): `EB 8F` is a short jump from `+117` back to `+6`, so execution enters the freshly read payload. Two payloads were used:
- **marker** printing `[STAGE9_NATIVE_SHORTTOKENS_OK]` via `write(1, msg, n); exit(0)` - proves remote execution and visible stdout;
- **ORW**: `open("/flag") - read(fd, buf, 0x100) - write(1, buf, n) - exit(0)`, falling back across `/flag`, `/flag.txt`, `/challenge/flag`.

## Tooling / environment gotchas (Kali)

- `qemu-i386` + 32-bit libc to run the ELF locally.
- Python venv with `language_tool_python` + the original `validate.py`.
- **Gotcha 1:** the binary spawns `execl("python3", "./validate.py", ...)`. On this box `python3` was shadowed by an AppImage, so the child had no LanguageTool and always "Rejected". Fixed with a **`python3` shim on `PATH`** pointing at the venv python.
- **Gotcha 2:** the remote `read()` effectively wants **exactly 900 bytes** - send `899 manuscript + '\n'`. Sending only 899 blocks indefinitely.

## Verification

The attack can be fully repeated only on a live instance (closed after the event), but the builds are deterministic and were verified offline:

```text
build(baseline, 'Sugar', 'rain', b'.R.A.BA') = evidence/manuscript_accepted_899.txt   - True
marker_stage2()                              = evidence/stage2_marker.bin            - True
baseline[605:609] = accepted[605:609] = b'able'                                      - True
```

Remote proof is captured in the raw replies:

- `evidence/remote_marker_probe.raw` - the server echoed the marker after the CS-free stage 1:
  `... Accepted by the anti-slop desk. Typesetting... [STAGE9_NATIVE_SHORTTOKENS_OK]`
- `evidence/remote_flag_orw.raw` - the ORW stage 2 printed the flag (masked here):
  `... FortID{Sh3llc0d1n6_...}`

## Dead ends (what I tried first)

1. **Pure QEMU validation.** The chain "worked" under `qemu-i386` but died on the native server; the CS-prefix store was the difference. Lesson: verify on the real target, not only the emulator.
2. **Long glued token blocks.** Blocks like `.RABPAX` formed words that LanguageTool flagged as misspellings; splitting with dots into short tokens fixed it.
3. **Comma in the prologue.** ` AG AO AW,ax:` triggered a permanent comma-whitespace finding; removing the comma and restructuring the prologue fixed it.
4. **Repeated content words.** The repetition ratio threshold (<=0.1) forced synonym swaps of equal length to keep offset 605 intact.

## One-paragraph version

Editorial Board is an i386 pwn challenge where the payload must be both valid English prose (LanguageTool grader, score >= 88) and a working exploit, using only printable ASCII. The binary reads a manuscript, copies it into a fixed RWX region and then into a stack buffer without bounds checks (off set to saved EIP = 605); bytes 605-608 = "able" make EIP = 0x656c6261, back into the RWX copy of our text. The allowed characters are all x86 opcodes, so the shellcode is self-modifying printable-ASCII: it patches its own bytes to synthesize forbidden opcodes (pop ebx, int 0x80). Two blockers were solved: a CS-prefixed self-store that faulted on native x86 (but not QEMU) was rewritten without the prefix, and the manuscript was tuned against LanguageTool (comma-free prologue, dot-split short tokens, synonym swaps) to reach score 89.45 ACCEPT. Stage 1 does read(0, EIP+6, 115), stage 2 is the ORW open("/flag") - read - write shellcode that prints the flag.

## Lessons learned

1. When the input charset is also the opcode space, the exploit is a constraint puzzle: every byte must be both a machine instruction and an allowed character.
2. Self-modifying shellcode is the standard answer when needed opcodes are not in the allowed set: zero a byte, then add the wanted value into it.
3. Emulators are optimistic: QEMU user-mode skipped a segment-prefix fault that native x86 takes. Always confirm on the real target.
4. A grammar checker as a security gate is bypassable with exactly the same care: keep tokens short, avoid punctuation the checker dislikes, and keep content words varied.
5. Repetition of equal-length words is a tool: synonyms of identical length let you fix the "editorial" side without moving machine-code offsets.

## Files

- `assets/editorial_board` - the 32-bit ELF (stripped), as shipped
- `assets/validate.py` - the original grader
- `scripts/editorial_stage9_shorttokens.py` - final: builds the accepted manuscript + stage 2, sends remotely
- `scripts/editorial_stage9_commafree.py`, `scripts/editorial_native_grammar_repair.py`, `scripts/editorial_native_cs_fix.py`, `scripts/editorial_board_live_solver.py` - iterations and diagnostics
- `evidence/` - raw artifacts: accepted manuscript, stage2 marker, remote marker/flag replies
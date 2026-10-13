# Editorial Board - writeup (i386 printable-ASCII shellcode hidden in valid English prose)

## Getting to know the task

The handout is two files:

- `editorial_board` - a 32-bit ELF, stripped (no symbols)
- `validate.py` - the validator that the binary spawns

The service asks for one English "manuscript" (<=900 bytes), validates it, and if the text is accepted, copies it into an RWX `mmap` region and then into a stack buffer with an unchecked `rep movsb`. The program then returns - and returns into our text.

The twist that makes this challenge special: the payload must be **valid English prose** that the grader accepts AND a working exploit. Only `[A-Za-z]` and ` .,;:'-?!` are allowed in the text (the binary rejects anything else). So the exploit is written as **self-modifying printable-ASCII i386 shellcode embedded in an otherwise grammatical English paragraph**.

## How I read the binary and the validator (which tools)

- `file editorial_board` - 32-bit ELF, i386, stripped. So no symbol names; I worked from disassembly.
- `objdump -d -M intel editorial_board` - the disassembly. I looked for: where the input is read, what happens after validation, and where the text ends up in memory.
- `strings editorial_board` - the prompts ("Submit your manuscript:", "The board is reviewing your manuscript..."), which tell what each syscall does.
- `validate.py` (given) - the grading rules, so I knew exactly what the text has to satisfy before the exploit even runs.
- `qemu-i386` + 32-bit libc - to run the ELF locally (the binary is i386 on an x86-64 box).

## The vulnerability (step by step)

The goal: our input is up to 900 bytes of text; to get the flag we must make the process execute our instructions (which will open `/flag` and print it). So the questions are: where does our input land in memory, can we hijack control flow, and at what address will our input execute?

### Step 1. Where the input is read (objdump)

Disassembling `main` with `objdump -d -M intel`, I found the input read:

```text
read(0, [ebp-0x399], 0x384)
```

`0x384` = 900: the manuscript is read into a 900-byte stack buffer. Nothing surprising yet - the question is what happens to it after validation.

### Step 2. What happens when the validator accepts (objdump)

After the validator returns "accepted", `main` does three things (all visible in the disassembly):

1. `memcpy(RWX, buf, n)` - a copy of our text is placed into a memory region created by `mmap`.
2. It calls a function at `0x804979e`, which copies the text **again** - this time into the stack.
3. Then the program returns.

The `mmap` call is the first important discovery:

```text
mmap(0x656c6000, 0x10000, PROT_READ|WRITE|EXEC, MAP_FIXED|ANON, -1, 0)
```

**What an RWX region is:** a memory page with all three protections at once - Read, Write, eXecute. Normally a page is either executable (so it cannot be written) or writable (so it cannot be executed) - that is the W^X rule. An RWX page allows both, which means: write shellcode into it, and then execute it from the same address.

**How I learned the address is fixed:** the first argument of `mmap` is the constant `0x656c6000` (in the disassembly it is pushed as an immediate value, not read from anywhere), and the `MAP_FIXED` flag is set. `mmap` with an explicit address plus `MAP_FIXED` means "map it exactly at this address", overriding ASLR. So the region is always at `0x656c6000` on every run.

**What this gives us:** no address leak is needed. We know the base in advance, and since our manuscript is copied to the start of that region, the byte at manuscript offset `N` will execute at address `0x656c6000 + N`. That makes the return address computable in advance (see Step 4).

### Step 3. The vulnerable function (objdump, step by step)

After the `memcpy` into RWX, `main` calls `0x804979e`. I went there to find where our input can overflow into the return address. Disassembling just that function:

```text
0x804979e:  push ebp; mov ebp, esp
            sub esp, 0x259              ; a 0x259-byte local buffer
            lea edi, [ebp-0x259]        ; destination of the copy
            mov esi, <our buffer>
            mov ecx, <length n>
            rep movsb                   ; copies n bytes, no length check
            leave
            ret
```

**How I recognized it as the vulnerability:** `rep movsb` copies exactly `n` bytes to `[ebp-0x259]`, and `n` can be up to 900 (the whole manuscript), while the buffer is only `0x259` = 601 bytes. There is **no bound check** - so the copy overflows past the buffer into the saved frame pointer and the saved return address.

**The math:** the buffer occupies `0x259` bytes below `[ebp]`. The saved EBP is at `[ebp]`, the saved return address at `[ebp+4]`. So a write starting at `[ebp-0x259]` reaches the saved EIP after `0x259 + 4` = `0x25d` = **605** bytes.

**Result:** if manuscript bytes 605..608 form a valid address, then after `ret` the program jumps there.

### Step 4. Choosing the return address (why "able")

We control where EIP goes. The RWX region is at `0x656c6000` and our manuscript is mapped at its start, so manuscript offset `N` executes at `0x656c6000 + N`. The exploit code starts right after the saved EIP, i.e. at offset 609. So we want EIP = `0x656c6000 + 609` = `0x656c6261`.

Now write those bytes as characters: `0x65='e'? no - 0x656c6261 little-endian is the bytes `61 62 6c 65` = `a b l e`. So setting the bytes at offsets 605 to 608 to `able` makes EIP = `0x656c6261`, and execution starts at offset 609 of the RWX region - the beginning of our shellcode.

### Step 5. One more property (setvbuf)

The disassembly shows `setvbuf(stdout, NULL, _IONBF, 0)` (and the same for stdin): unbuffered output. So `write(1, ...)` from our shellcode is visible on the socket immediately, without any flush. This matters for the flag exfiltration path.

## The constraint: every allowed character is an x86 opcode

The allowed set `[A-Za-z]` + ` .,;:'-?!` was chosen because in x86 every such byte is a valid opcode. I did not take that on faith - I verified it byte by byte: I wrote a small Python script that emits a buffer `[candidate byte] + [0x90 NOPs]` for each allowed character, ran `objdump -D -b binary -m i386` on it, and read off the mnemonic of the first instruction. The full table I built:

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

**Why this table matters for the flag:** it is the vocabulary of instructions we can print directly in the manuscript. Comparing it with what the shellcode needs (`sys_read`, `int 0x80`, `open/read/write`), two required bytes are missing: `0x5b` (`pop ebx`) and `0xcd 0x80` (`int 0x80`). So the table tells us exactly what we cannot type - and therefore what we have to synthesize at runtime (next section).

## Self-modification (and where the technique comes from)

We cannot type `0x5b` or `0xcd 0x80`, but the shellcode needs them. The solution is not a flash of inspiration - it is a known technique that goes back to the early days of shellcoding: **self-modifying code**. I remembered it from the classic printable-ASCII shellcode writeups and from the shellcode chapters of "Hacking: The Art of Exploitation" (the "polymorphic/self-modifying shellcode" section), where code is arranged so that it patches its own bytes at runtime - exactly for cases like this, when forbidden bytes cannot appear in the payload file.

The crucial enabler here: the manuscript is executed from an **RWX** page (Step 2). If the page were read-only while executing, a write into it would fault. Because the region is writable, our code can modify itself.

**How the self-modification works:** the prologue zeroes `AL` (`sub al, imm` / `xor eax,eax`), then uses `AND BYTE PTR [ECX+disp], AL` (the bytes `0x00 41 dd`, written as `.`/`A` plus a displacement) to zero a target opcode byte in the mapped manuscript. With AL=0 the AND zeroes that byte; subsequent `SUB AL, imm8` sequences build AL up to the wanted value, and a final `ADD BYTE PTR [ECX+disp], AL` writes the desired opcode. This way forbidden bytes like `0x5b` (`pop ebx`) and `0xcd 0x80` (`int 0x80`) are synthesized, not typed.

## The critical native-x86 bug (why it worked under QEMU but not on the server)

The first "working" chain ran fine under `qemu-i386` but **silently failed on the real server**: the manuscript was accepted, but no output appeared.

Root cause (I disassembled the patched bytes of the running buffer and looked at the byte at the write site): one of the self-modifying stores decoded as

```text
2e 00 41 xx    add byte ptr cs:[ecx+xx], al
```

i.e. an **ADD through the CS segment prefix**. On native protected-mode x86 a `CS:` memory write raises `#GP` (general protection fault), regardless of whether the page is RWX. QEMU user-mode does not enforce this. So the exploit "worked" locally (under QEMU) and died remotely (native x86).

**Fix:** drop the CS prefix from that store - emit the plain

```text
00 41 62       add byte ptr [ecx+0x62], al
```

and compensate the removed prefix byte by two extra `inc ecx` so the store still targets the same byte. Confirmed remotely: the server printed the marker, proving the first stage executed natively.

> Lesson: never trust `qemu-i386` for the correctness of segment-prefix / faulting instructions. Validate on the real target.

## The editorial (LanguageTool) countermeasures - the hardest part

**What the grader is:** `validate.py` is a separate program that the binary spawns (Step 2). It is not a security check - it is an "editorial board" that decides whether the manuscript is good English. It runs the text through LanguageTool, an open-source grammar and spell checker, and computes a score from the findings. If the score is below 88, or there is any spelling finding, the text is rejected and the exploit never runs.

**What "tokens" mean here:** LanguageTool does not read the text as a blob - it splits it into **tokens** (words, punctuation marks, spaces) and runs its rules over them. Our "manuscript" is actually machine code, so many of its "words" are nonsense like `RABPAX` or `AZ-Sugar:`. To the grammar checker those look like typos, and each typo is a finding that lowers the score.

**Why changing a machine byte "perturbs the token stream":** every machine byte is also a text character. When we change shellcode bytes (to fix a bug or add an instruction), we change letters and punctuation in the "sentence", which changes the tokens: new nonsense words appear (`RABPAX`), or a comma suddenly sits where the checker expects a space (`COMMA_PARENTHESIS_WHITESPACE`), or a content word repeats too often (the repetition ratio). Each such finding drops the score; below 88 the manuscript is rejected. So we are solving two coupled puzzles at once: the bytes must be a working program AND a text the editor accepts.

The steps that finally passed:

1. **Remove the comma in the prologue.** The original ` AG AO AW,ax:` caused a permanent `COMMA_PARENTHESIS_WHITESPACE` finding (the checker saw a comma not followed by a space). Replaced with ` AJ AR AZ-<Word>:` where `-<Word>` is `sub eax, imm32` and the trailing char is a taken conditional jump.
2. **Short, dot-separated tokens.** A block like `.RABPAX` forms the "word" `RABPAX`, which LanguageTool flags as `MORFOLOGIK_RULE_EN_US` (a misspelling). Replacing it with **`.R.A.BA`** (dots between ops - short tokens, each a single letter) removes the spelling finding.
3. **Reduce repetition** by swapping repeated content words for synonyms of identical length (so the offset 605 is preserved): `soft` to `thin`, `team` to `crew`, `brief` to `short`, `can` to `may`/`did`.

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

i.e. `read(0, EIP+6, 115)` - pulls a raw binary **second stage** from the socket into executable memory. (The first stage is limited to the printable alphabet, so it cannot contain the full `open/read/write` chain comfortably; the second stage is raw bytes and can be anything.) 

**Stage 2** (115 raw bytes, built as `stage2 = core + NOPs + EB 8F`): `EB 8F` is a short jump from `+117` back to `+6`, so execution enters the freshly read payload. Two payloads were used:
- **marker** printing `[STAGE9_NATIVE_SHORTTOKENS_OK]` via `write(1, msg, n); exit(0)` - proves remote execution and visible stdout;
- **ORW**: `open("/flag")`, then `read(fd, buf, 0x100)`, then `write(1, buf, n)`, then `exit(0)`, falling back across `/flag`, `/flag.txt`, `/challenge/flag`.

## Tooling / environment gotchas (Kali)

- `qemu-i386` + 32-bit libc to run the ELF locally.
- Python venv with `language_tool_python` + the original `validate.py`.
- **Gotcha 1:** the binary spawns `execl("python3", "./validate.py", ...)`. On this box `python3` was shadowed by an AppImage, so the child had no LanguageTool and always "Rejected". Fixed with a **`python3` shim on `PATH`** pointing at the venv python.
- **Gotcha 2:** the remote `read()` effectively wants **exactly 900 bytes** - send `899 manuscript + '\n'`. Sending only 899 blocks indefinitely.

## Verification

The attack can be fully repeated only on a live instance (closed after the event), but the builds are deterministic and were verified offline:

```text
build(baseline, 'Sugar', 'rain', b'.R.A.BA') == evidence/manuscript_accepted_899.txt   - True
marker_stage2()                              == evidence/stage2_marker.bin            - True
baseline[605:609] == accepted[605:609] == b'able'                                      - True
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

Editorial Board is an i386 pwn challenge where the payload must be both valid English prose (LanguageTool grader, score >= 88) and a working exploit, using only printable ASCII. The binary reads a manuscript, copies it into a fixed RWX region and then into a stack buffer without bounds checks (offset to saved EIP = 605); bytes 605-608 = "able" make EIP = 0x656c6261, back into the RWX copy of our text. The allowed characters are all x86 opcodes, so the shellcode is self-modifying printable-ASCII: it patches its own bytes to synthesize forbidden opcodes (pop ebx, int 0x80). Two blockers were solved: a CS-prefixed self-store that faulted on native x86 (but not QEMU) was rewritten without the prefix, and the manuscript was tuned against LanguageTool (comma-free prologue, dot-split short tokens, synonym swaps) to reach score 89.45 ACCEPT. Stage 1 does read(0, EIP+6, 115), stage 2 is the ORW open("/flag"), read, write shellcode that prints the flag.

## Lessons learned

1. When the input charset is also the opcode space, the exploit is a constraint puzzle: every byte must be both a machine instruction and an allowed character.
2. Self-modifying shellcode is the standard answer when needed opcodes are not in the allowed set: zero a byte, then add the wanted value into it. The technique is decades old - reuse it instead of reinventing.
3. Emulators are optimistic: QEMU user-mode skipped a segment-prefix fault that native x86 takes. Always confirm on the real target.
4. A grammar checker as a security gate is bypassable with exactly the same care: keep tokens short, avoid punctuation the checker dislikes, and keep content words varied.
5. Repetition of equal-length words is a tool: synonyms of identical length let you fix the "editorial" side without moving machine-code offsets.
6. An RWX region is a gift for shellcoding: no need to defeat W^X, and a fixed (MAP_FIXED) address means no leak is needed - just compute the return address from a constant.

## Files

- `assets/editorial_board` - the 32-bit ELF (stripped), as shipped
- `assets/validate.py` - the original grader
- `scripts/editorial_stage9_shorttokens.py` - final: builds the accepted manuscript + stage 2, sends remotely
- `scripts/editorial_stage9_commafree.py`, `scripts/editorial_native_grammar_repair.py`, `scripts/editorial_native_cs_fix.py`, `scripts/editorial_board_live_solver.py` - iterations and diagnostics
- `evidence/` - raw artifacts: accepted manuscript, stage2 marker, remote marker/flag replies
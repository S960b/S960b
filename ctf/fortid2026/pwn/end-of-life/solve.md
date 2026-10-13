# End of Life - writeup (pwn, 16-bit DOS, stack overflow)

## Getting to know the task

Task description: "MS Windows 10 has reached its (endoflife.date), but MS DOS is still going strong."

I unpacked the archive. Four files inside:

- `CHALL.CPP` - the C source (Borland C++ 3.0, an old DOS-era compiler)
- `CHALL.EXE` - the compiled 16-bit DOS program (14 KiB)
- `RUN.BAT` - a batch file that just starts CHALL.EXE
- `server.py` - the server that runs this program remotely

First I read `server.py` - it is the spec of how the challenge works. It does this:

1. Takes one line: our file, base64-encoded.
2. Creates a temp directory under `./run`.
3. Copies the secret `flag.txt` there as `FLAG.TXT`, plus `RUN.BAT` and `CHALL.EXE`.
4. Decodes our base64 line and writes it as `INPUT.TXT` in the same directory.
5. Runs the DOS emulator: `dosbox -c "MOUNT C <dir>" -c "C:\RUN.BAT"` (the DOS program sees that directory as its C: drive).
6. If the program created `OUTPUT.TXT`, the server returns its contents in base64. Otherwise it prints "Program did not produce OUTPUT.TXT".

Key takeaways:

- The secret flag sits next to our program on drive C:, as a plain file `FLAG.TXT`.
- The only way to get data out is to make the program write something to `OUTPUT.TXT`.
- The server gives the program 10 seconds (`signal.alarm(10)`).

## Reading the source: looking for where the program can go wrong

`CHALL.CPP` is short, readable in a minute. The important part is two functions.

The first one, `check_input()`, validates the input file:

```c
int check_input() {
    FILE* input = fopen("input.txt", "rt");   // opened in TEXT mode
    ...
    while ((c = fgetc(input)) != EOF) {
        if (c < 'a' || c > 'z') return 0;      // lowercase letters only
        if (length >= 16) return 0;            // and at most 16 of them
    }
    ...
}
```

The second one, `process_input()`, processes the same file:

```c
void process_input() {
    FILE* input = fopen("input.txt", "rb");   // opened in BINARY mode
    FILE* output = fopen("output.txt", "wb");
    char name[17];                            // a 17-byte buffer
    fscanf(input, "%[^ ]s", name);            // read a "word" into the buffer
    fprintf(output, "%d\n", strlen(name));    // print its length
}
```

Here is what caught my eye: the same program opens THE SAME FILE twice - once as text (`"rt"`), once as binary (`"rb"`). That is rarely done without a reason. I asked myself: are these two modes even equivalent? Could one function see the file one way and the other a different way?

## Bug 1: text and binary modes disagree on byte 0x1A

I recalled that old DOS text files had quirks and decided to check. I searched for "fopen text mode binary mode difference" and found the documentation:

- cppreference, fopen: https://en.cppreference.com/c/io/fopen - it says that in text mode reads stop at EOF, and on some systems the byte 0x1A (Ctrl-Z, the DOS end-of-file character) is treated as end of file;
- Stack Overflow, "Difference between files written in binary and text mode": https://stackoverflow.com/questions/229924 - it states directly that DOS text-mode reading stops at Ctrl-Z (character 26 = 0x1A), while binary mode reads it as an ordinary byte.

There is the logical bridge: `check_input()` opens the file as text, so it STOPS at the first 0x1A byte, believing the file ended. `process_input()` opens the same file as binary and calmly reads everything, including the bytes AFTER the 0x1A.

What does that give us? The validator (`check_input`) sees only the part before 0x1A, the processor (`process_input`) sees the whole file. If I put 16 lowercase letters at the start (so the validator lets it through), then a 0x1A byte, then anything - the validator passes the file, and the processor reads my tail too.

## Bug 2: fscanf reads into the buffer with no length limit

Now look at `process_input()`: `fscanf(input, "%[^ ]s", name)`.

What is `%[^ ]`? It is the "scanset" specifier of the scanf family. Square brackets define a set of allowed characters, and `^` at the start negates it: "any character EXCEPT the listed ones". So `%[^ ]` reads characters until it meets a SPACE (0x20). Documentation: cppreference, fscanf: https://en.cppreference.com/w/c/io/fscanf (the scanset section).

Important consequences:

- There is NO length limit in the format (it is not `%16[^ ]`), and the `name` buffer is only 17 bytes. So if the "word" in the input file is longer than 17 characters, `fscanf` writes it all and runs past the end of the buffer - a stack overflow.
- Only a space stops the reading. Bytes 0x00 (NUL), newline, everything else - are just copied into the buffer.

There is the attack scheme: 16 letters + 0x1A + a tail with no spaces. The validator lets it through (sees 16 letters, then "end of file"), the processor reads it all, and the tail overflows `name[17]`. In the tail, only the space is forbidden.

## Experiment: which bytes actually arrive (step by step)

Before building the exploit I confirmed in practice how the program treats different bytes. The experiment is simple: the program prints the length of the read word (`strlen`) into OUTPUT.TXT, so the number tells me where `fscanf` stopped and what got through.

Step by step:

1. Created a directory, put `CHALL.EXE` and `RUN.BAT` into it.
2. Created `INPUT.TXT` in it with the bytes I wanted to test.
3. Ran DOSBox with the same command the server uses: `dosbox -c "MOUNT C ." -c "C:\RUN.BAT"`. Since I have no root, I installed DOSBox without root: downloaded the .deb packages and unpacked them into my own directory (`apt-get download`, `dpkg-deb -x`), and run with `LD_LIBRARY_PATH=... SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy`.
4. Opened `OUTPUT.TXT` and read what it printed.

Probe 1: INPUT.TXT = `aaaa` + byte 0x1A + `BBBB`. The validator sees `aaaa` (4 letters, then "end of file") and lets it through. The processor in binary mode reads `aaaa` + 0x1A + `BBBB` - 9 bytes until space/EOF. OUTPUT.TXT = `9` - so `fscanf` read all 9 bytes. Bug 1 confirmed: 0x1A did not stop the processor.

Probe 2 (NUL): INPUT.TXT = `aaaa` + 0x1A + byte 0x00 + `BBBB`. OUTPUT.TXT = `5`. Conclusion: NUL DOES arrive in the buffer (otherwise the length would be 9), and it terminates the C string (`strlen` counts up to the first zero) - so NUL can be used in the payload, but it ends the string.

Probe 3 (newline): INPUT.TXT = `aaaa` + 0x1A + 0x0A + `BBBB`. OUTPUT.TXT = `10` - the LF arrived and did NOT stop the reading.

Probe 4 (space): INPUT.TXT = `abc` + 0x1A + ` def`. OUTPUT.TXT = `4` - fscanf stopped at the space (it read `abc` + 0x1A = 4 bytes).

Payload rules, established: 0x1A is the split for the validator; 0x00 is delivered (and ends the C string); LF is delivered; 0x20 (space) is the only forbidden byte.

## What a stack frame is (explained for a beginner)

To understand where exactly the buffer overflows into, I had to know how the program lays out its local variables.

When a function is called in C, the CPU uses the stack - a memory region that grows "downward". Each called function gets a block on the stack - a stack frame - holding: the return address (where to resume after the function finishes), the saved frame pointer, and the local variables. In 16-bit programs two registers usually point at the frame: BP (base pointer - the start of the frame) and SP (stack pointer - the top of the stack). Local variables sit at negative offsets from BP.

"Mapping the frame" means finding exactly where each local variable (and the return address) sits relative to BP. This tells me how many overflow bytes are needed to walk from the buffer to the return address.

How I mapped it: with a disassembler. A disassembler converts the program's machine bytes back into assembly instructions. I used `ndisasm` (from the nasm package): first I found that the MZ header of this file is 512 bytes, then ran `ndisasm -b16 -e512 CHALL.EXE` (skip the header, disassemble as 16-bit code). In the disassembled `process_input` I saw:

```text
push bp              ; save the old BP
mov bp, sp           ; BP = start of our frame
sub sp, 0x12         ; reserve 18 bytes for locals
...
lea ax, [bp-0x12]    ; compute the address of name
push ax
push 0xD2            ; address of the "%[^ ]s" string
push si              ; FILE* input
call fscanf
...
```

The instruction `lea ax, [bp-0x12]` is the key: it computes the address of the `name` buffer. So `name` sits 18 bytes below the frame start, occupying `[bp-0x12] .. [bp-0x12+17]`. Right after it (higher up the stack): the saved BP at `[bp]` (2 bytes), then the return address at `[bp+2]` (2 bytes). From the start of `name` to the return address is 20 bytes (0x12 = 18 + 2). So if more than 17 bytes are written into `name`, bytes 18-19 clobber the saved BP, and bytes 20-21 clobber the return address.

One more important fact about this program: its stack is unusual. The MZ header declares its own stack segment, but the Borland runtime performs an explicit stack switch at startup: `mov ss,dx; mov sp,di`, where DX = the data segment (DGROUP). So at runtime the stack lives IN THE SAME SEGMENT as the data (`SS == DS`). This will turn out to be key for the exploit.

## How I learned BP and SS without a debugger

To compute the addresses in the payload exactly, I needed the live values of two registers at the moment `process_input` runs: BP (frame start) and SS (stack segment).

I had no scriptable debugger under DOSBox. I took a side route: made THE PROGRAM ITSELF print those registers. At the end of `process_input` stands `fprintf(output, "%d\n", strlen(name))` - so before that call the strlen result is pushed onto the stack. In machine bytes that is `push ax` (opcode 0x50), located at file offset 512+0x32F (0x32F is the offset within the code, 512 is the MZ header size).

How I patched: a simple Python script - read the file as bytes, replace ONE byte at the needed offset, write a new copy. No fancy tools. Two copies:

- `CHALL_BP.EXE` - replaced 0x50 with 0x55 (`push bp`): now fprintf prints BP instead of the length
- `CHALL_SS.EXE` - replaced 0x50 with 0x16 (`push ss`): now it prints SS

I ran each in DOSBox with INPUT.TXT = `hello` and read OUTPUT.TXT. The number there is a signed decimal; I convert it to 16 bits with `& 0xFFFF`:

```text
CHALL_BP: -14   = 0xFFF2
CHALL_SS:  993  = 0x03E1
```

Important: these patched copies are for measurement only. They never go to the server - the original CHALL.EXE runs there; the plain number BP is used for the payload math.

## The exploit: turning the overflow into code execution

Now the main part. The overflow clobbers the return address. When the function finishes, the `ret` instruction pops the return address from the stack and jumps to it. If we wrote our own value there, we control where the program jumps.

But there is a 16-bit architecture subtlety: in DOS an address has TWO parts - segment and offset (CS:IP). A plain `ret` (near return) jumps only within the same code segment: it pops 2 bytes and puts them into IP, leaving CS untouched. There is also `retf` (far return) - it pops 4 bytes and loads both IP and CS, so it can jump into ANOTHER segment.

Why near return is not enough: our shellcode sits on the stack, and the stack (we found out) lives in the data segment DGROUP. Offsets inside that segment are counted from the start of DGROUP, and with a high-placed stack that number is near 0xFFFF or above. IP is 16-bit, max 0xFFFF, so the address "wraps" - overflows - into a small number, missing our code. In plain words: a near-`ret` can only jump "inside" the current 64 KiB code window, and our code lives in another window - it physically cannot reach it.

A `retf`, however, can jump into another segment: we set both CS (= SS = 0x3E1, the stack segment) and IP (the shellcode offset) ourselves. Plan: write the address of an existing `retf` instruction into the return address slot, and right behind it our CS and IP values.

CHALL.EXE contains ready-made `retf` instructions (machine code CB) - I found them by scanning the bytes: at offsets 0x986, 0x1141 and 0x14DC. Let us take 0x986.

What is "position-independent code"? It is code that works no matter where it is loaded: it does not use hardcoded addresses, instead it computes its own location on the fly. The first instruction of our shellcode is `call $+5; pop si`: `call` (short, 2 bytes) jumps to the next instruction while pushing that next instruction's address onto the stack, and `pop si` takes it into register SI. Now SI = where we are in memory, and all string references are "SI + offset". So I do not need to know in advance at which address the shellcode will land - it finds itself.

The shellcode does exactly what a DOS command "copy FLAG.TXT to OUTPUT.TXT" would do, but through INT 21h system calls:

1. Open `FLAG.TXT` (INT 21h, function 3Dh)
2. Read up to 256 bytes (3Fh)
3. Create `OUTPUT.TXT` (3Ch)
4. Write what was read (40h)
5. Close (3Eh), exit (4Ch)

The strings "FLAG.TXT" and "OUTPUT.TXT" sit right after the code, addressed via SI+offset. And not a single 0x20 (space) byte anywhere in the payload, or fscanf would stop early - I checked that with a script.

The full payload layout (INPUT.TXT):

```text
bytes 0..15    'a' x 16          passes the validator (16 letters)
byte 16        0x1A              split: the validator stops here
byte 17        'Z'               padding
bytes 18..19   'QQ'              clobber the saved BP (we do not care what is there)
bytes 20..21   0x86 0x09         return address = retf gadget (0x986, little-endian)
bytes 22..23   low 2 bytes of the shellcode IP (computed from BP)
bytes 24..25   0xE1 0x03         CS = SS = 0x3E1
bytes 26..31   'Z' x 6           padding up to the shellcode
byte 32 and on                  the shellcode itself
```

The shellcode offset math: `name` starts at `[bp-0x12]`, the shellcode starts `where=32` bytes into the overflow, so its address = `bp - 0x12 + 32` = `0xFFF2 - 0x12 + 32 = 0x10000`, which truncates to 16 bits = `0x0000`. Yes, formally the IP wraps to 0. And yet the shellcode EXECUTES correctly - I verified it multiple times, the result is deterministic. This is a strange spot: the "linear address = segment*16 + offset" model explains it by landing on the same linear byte after the wrap. I did not dig into explaining it completely - empirics beat theory here, and the chain works reliably.

## Local verification: step by step

Before going to the server, I verified everything locally:

1. Created a test directory.
2. Put the original `CHALL.EXE`, `RUN.BAT`, and the built `INPUT.TXT` (payload) into it.
3. Put a synthetic flag next to them: a file `FLAG.TXT` containing `FortID{LOCAL_SYNTHETIC_dos_7e21}` - exactly like the server does it.
4. Ran DOSBox with the same command as the server: `dosbox -c "MOUNT C ." -c "C:\RUN.BAT"`.
5. Opened `OUTPUT.TXT`.

Result: `OUTPUT.TXT` = `FortID{LOCAL_SYNTHETIC_dos_7e21}` - an exact copy of the flag. The shellcode worked, read FLAG.TXT and wrote it into OUTPUT.TXT.

Then the negative control - the same steps but WITHOUT the FLAG.TXT file. `OUTPUT.TXT` came out EMPTY. This matters: it proves the text in OUTPUT.TXT is not random garbage or a "phantom" - it is the result of actually reading the file. If the shellcode wrote something unconditionally, the output would not be empty.

And the final local check - the whole loop through the REAL server.py from the platform (I only replaced the dosbox path inside it with the local one, because my dosbox is installed without root in /tmp):

1. Put `server.py`, `CHALL.EXE`, `RUN.BAT` into a directory, created `flag.txt` there with a synthetic flag.
2. Started the server, sent it base64(payload) as one line.
3. The server replied `Your OUTPUT.TXT: <base64>`.
4. Decoded - got the synthetic flag.

The chain is proven locally end to end.

## Remote

Run the same payload against the remote instance (`end-of-life-<...>.inst20.ctf.fortid.com:1337`, TLS). The client script connects, sends base64(payload), receives the reply. The server replied `Your OUTPUT.TXT: <base64>`, decoded - the flag: `FortID{3oF_15_JU57_4_5u993571On_<hash>}` (masked in this writeup).

## What I tried first (dead ends)

1. **I thought it was a format string.** With large overflows (250+ bytes) OUTPUT.TXT started echoing my bytes in a strange pattern (output length = N-249, printing starts at offset 197). I planted harmless `%c`/`%x` markers at those spots: they did NOT expand, so it is not a format string - the overflow corrupts the FILE structure internals (its buffer/length fields) and fprintf just prints raw bytes. An important negative result: it steered me away from `%n`-style writes.

2. **Direct near-return into the shellcode.** The logical first option - just write the shellcode address into the return address slot. But a near-`ret` cannot jump into another segment (and the stack lives in the data segment), and due to the 16-bit address wrap it never landed in the code. Hundreds of runs with different BP values - zero hits. This is exactly what pushed me to look for the far path (retf).

3. **ret2code: jumping into the existing fopen.** Another option: jump not into shellcode but into the existing `fopen` and feed it arguments from the stack. But precise argument offsets need the numeric BP, which depends on the top of memory (PSP:2), unreadable at runtime. The calibration patch solved the same need more simply.

4. **Unicorn emulator as a debugger.** I tried emulating the 16-bit code with Unicorn to watch registers. It loaded the MZ and hooked INT 21h, but the Borland startup never reached `main` (it looped over INT 21h calls and exited). Documented as a blocker; in the end it became unnecessary - the program was made to print its own registers.

## Final scripts

- `scripts/calibrate.py` - patches CHALL.EXE (changes one byte via Python) so fprintf prints BP or SS; run in DOSBox with INPUT.TXT = hello, read OUTPUT.TXT, convert with & 0xFFFF. For this build: BP=0xFFF2, SS=0x3E1.
- `scripts/build_payload.py` - builds INPUT.TXT: takes BP and SS, iterates shellcode positions and retf gadgets until it finds a layout without a space; appends the PIC shellcode; writes INPUT.TXT and INPUT.TXT.b64.
- `scripts/solve.py` - the client: TLS, sends base64(payload), prints the server reply and the decoded OUTPUT.TXT.

## Result

Remote run log: the server sent `Enter contents for INPUT.TXT (base64):`, we sent one base64 line, the server replied `Running /usr/bin/dosbox ...` and `Your OUTPUT.TXT: <base64>`. The decoded value is the flag.

## One-paragraph version

The server runs our uploaded file as INPUT.TXT under DOSBox next to FLAG.TXT and returns the base64 of whatever the program writes to OUTPUT.TXT. The DOS program has two bugs: the text-mode validator stops at byte 0x1A (that is how DOS text files work - I checked the documentation), while the binary-mode processor reads the whole file, and `fscanf("%[^ ]", name[17])` has no length limit and writes anything until a space into the 17-byte buffer. After mapping the stack frame with a disassembler (name 18 bytes below BP, return address at byte 20) and capturing BP/SS (the program printed them itself through a one-byte patch), we overflow the return address with an existing `retf` gadget and far-jump into position-independent 8086 shellcode on the stack that copies FLAG.TXT into OUTPUT.TXT via INT 21h. Verified locally on a synthetic flag (plus a negative control), then the same payload returned the flag from the real instance.

## Lessons learned

1. Read the source first: `CHALL.CPP` is ~50 lines, both bugs are visible by eye (two different fopen modes, and fscanf without a length limit).
2. If a function opens the same file in different modes, that is suspicious. DOS text mode stops at 0x1A, binary does not - a classic validator bypass. Check the documentation, do not rely on memory.
3. Before building a payload, verify empirically which bytes arrive: one experiment with NUL and LF saved hours.
4. Map the stack frame with a disassembler: find the `lea` with a negative offset from BP - that is a local variable's address; the rest is arithmetic.
5. No debugger? Make the program print its own registers: a one-byte `push` patch turns the existing fprintf into a register printer.
6. In Borland programs the runtime switches the stack into the data segment (SS == DS) itself. This both opens the far path and closes the near path (the 16-bit IP cannot reach a high stack).
7. A near-`ret` cannot leave its code segment; jumping into another segment needs `retf`. Ready-made `retf` instructions are found in the binary by scanning for byte CB.
8. Position-independent code (call/pop self-addressing) removes the need to know the load address in advance.
9. Not a single space byte is allowed in the payload when reading goes through `%[^ ]`. Scan the payload with a script before sending.
10. The negative control is mandatory: without FLAG.TXT the output is empty - so the result is not a phantom.
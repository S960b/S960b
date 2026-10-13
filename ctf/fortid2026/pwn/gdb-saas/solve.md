# GDB SaaS - writeup (pwn, DWARF source read)

## Getting to know the task

The service is called GDB SaaS: "Get your `main`s disassembled for free". You send it a binary and a function name, and it runs gdb on your behalf, disassembles the function and prints the output.

The attachment is a single file, `server.py`. I read it top to bottom. It does roughly this:

1. Ask for the ELF bytes, base64-encoded. Must be under 30 KiB.
2. Ask for a function name. Must be under 30 chars, and none of a long list of "bad characters" may appear in it: NUL, newline, tab, `; | & \` $ < > \ ' " ! # , ( ) [ ] { }`.
3. Create a temporary directory inside the server's working directory.
4. Copy `flag.txt` into that temporary directory.
5. Write our binary to `<tmpdir>/a.out`.
6. Run: `gdb -ex "disassemble <function_name>" -ex quit <tmpdir>/a.out`.

The key detail is in step 4: `flag.txt` is copied into the same directory that is used as the current directory for our binary. And gdb is started with the server's working directory as its cwd, not the temp dir.

## The idea: make gdb print the flag for us

I do not need a shell, and I do not need to break out of anything. The service already runs gdb on a binary I control. I only need to make gdb show the contents of a file that is not part of my binary.

gdb, when it disassembles a function compiled with debug info (`-g`), can show the source line next to the assembly. To do that it resolves the source file path from the debug info. The path is stored as two parts: the compilation directory (`DW_AT_comp_dir`) plus the file name.

So the plan: write a tiny C program whose debug info claims that its source file is `flag.txt` and its compilation directory is `.` (a dot, meaning "current directory"). Then gdb, running in the server's working directory, will look for `./flag.txt` - and the server has already copied the real `flag.txt` right there.

If that works, `disassemble /s main` will print the flag as the "source line" of main.

## Building the payload

Two compiler tricks are needed.

First, force the recorded source file name to be `flag.txt`. gcc supports line directives in the source: `#line 1 "flag.txt"` tells the compiler "from here on, pretend the source file is flag.txt". So the source file starts with that directive:

```c
#line 1 "flag.txt"
int main(void) {
    int x = 1;
    x += 1;
    return x;
}
```

Second, force the compilation directory to be `.`. By default gcc records the directory it was invoked from (its cwd) as `DW_AT_comp_dir`. The flag `-fdebug-prefix-map=<builddir>=.` rewrites the recorded path: every occurrence of the build directory is replaced by `.`. So the compile must run from inside the build dir:

```bash
cd /tmp/gdb_build
gcc -g -O0 -fdebug-prefix-map=/tmp/gdb_build=. -o elf.bin main.c
```

Before going further I checked the result with `readelf` (a GNU binutils tool that prints the ELF headers and the DWARF debug sections). It is easy to get the debug path wrong, and a wrong `DW_AT_comp_dir` means gdb looks in the wrong place. The command:

```bash
readelf --debug-dump=info elf.bin
```

shows the debug info tree. The two lines that matter:

```text
<17>   DW_AT_name        : (indirect line string, offset: 0x2): main.c
<1b>   DW_AT_comp_dir    : (indirect line string, offset: 0): .
```

`DW_AT_name` is the recorded source file name (`main.c`), `DW_AT_comp_dir` is the recorded compilation directory. The values live in the `.debug_line_str` string table, so readelf prints them as `(indirect line string, offset: N): <resolved value>`. The important part is after the second colon: the compilation directory resolved to `.` - exactly what we need. (The `DW_AT_name` stays `main.c`; gdb, when it cannot find `main.c`, falls back to the comp_dir-relative path, which is what we abuse.)

Now the size check. gdb-saas rejects inputs of `len(contents) >= 30*1024` characters, so the base64 must stay under 30720 characters. I measured both:

```bash
ls -la elf.bin
base64 elf.bin | wc -c
```

```text
-rwxrwxr-x 1 kali kali 16720 ... elf.bin     (file size: 16720 bytes = 16.7 KiB)
22590                                        (base64 length: 22590 characters)
```

base64 encodes every 3 bytes as 4 characters, so 16720 bytes become 22590 characters (~22 KiB). 22590 is under the 30720 limit with about 8 KiB to spare, so the payload fits.

## Choosing the function name

The function name must pass the character filter, and it is interpolated into the gdb command as `disassemble <name>`. The bad-character list forbids shell metacharacters and gdb command separators, but it does NOT forbid a space or a slash. That is the whole point.

gdb's `disassemble` accepts a `/m` or `/s` modifier before the location to mix in source lines. So the function name `/s main` becomes the command `disassemble /s main` - "disassemble main and show me the source". Neither `/`, the space, nor `main` are in the bad list, and the string is 8 chars, under 30.

## Verification, locally first

I set up a local copy of the server's environment: a directory with `flag.txt`, a subdirectory with `a.out`, and ran the exact gdb command the server runs:

```bash
gdb -ex 'disassemble /s main' -ex quit a.out
```

Output:

```text
Dump of assembler code for function main:
./flag.txt:
1   FortID{SIM_FLAG_gdb_77ab}
   0x0000000000001129 <+0>  push   %rbp
   ...
```

The flag appears as the first "source line" of main. Chain confirmed locally.

## What I tried first (dead ends)

1. **Looking for a way to read files through gdb commands.** The obvious idea: make the function name itself a gdb command, e.g. `disassemble; shell cat flag.txt`. Dead instantly: `;` and spaces are fine but `;` is in BAD_CHARS, and so are the shell metacharacters `$`, `|`, `&`. There is no semicolon-based command chaining here.
2. **Trying other gdb commands as the "function name".** Even without `;`, gdb's `-ex` executes only what follows `disassemble ` as a single location argument. The filter kills anything that could turn it into a new command. Reading the file had to happen *inside* the disassemble action itself - which is exactly what the `/s` modifier gives us.
3. **Absolute path in the ELF instead of `.`.** I first tried `-fdebug-prefix-map` to some absolute path like `/`, so gdb would look for `/flag.txt`. It did not exist on the server. The correct place is the server's working directory, where `flag.txt` is copied: that is `.` from gdb's point of view. Verifying with `readelf` caught this early.
4. **Compiling from a different cwd.** gcc records its own working directory as `DW_AT_comp_dir`, not the directory of the source file. My first build ran gcc from outside the build dir, so `DW_AT_comp_dir` was an absolute path and `-fdebug-prefix-map` never matched. readelf showed the problem, and running the compile from inside the build dir fixed it.

## Final scripts

- `scripts/build_payload.py` - creates the malicious ELF. Writes the source with the `#line 1 "flag.txt"` directive, compiles with `gcc -g -O0 -fdebug-prefix-map=<tmp>=.`, prints the output path. Run: `python3 build_payload.py elf.bin`.
- `scripts/solve.py` - the client. Connects over TLS, sends the base64 ELF, then the function name `/s main`, prints everything the server returns. Run: `python3 solve.py elf.bin` (host/port are in the script; remote instance is closed after the event).

## Result

The remote run (instance `gdb-saas-<...>.inst19.ctf.fortid.com`, gdb 17.1 on the server) returned:

```text
Reading symbols from /tmp/tmpnhj54rxv/a.out...
Dump of assembler code for function main:
./flag.txt:
1   FortID{...}
   0x0000000000001129 <+0>  push   %rbp
   ...
```

The flag is the first line of the disassembly. No shell, no command injection - gdb itself read the file because the debug info told it to.

## One-paragraph version

The service runs `gdb -ex "disassemble <our_string>"` on a binary we upload, in a working directory that contains flag.txt. The string filter blocks shell characters but not spaces or slashes, so we pass `/s main` and gdb disassembles main with source. The source path comes from the debug info, so we compile a tiny program whose file is recorded as `flag.txt` (`#line 1`) and whose compilation directory is recorded as `.` (`-fdebug-prefix-map`), verified with readelf. gdb resolves `./flag.txt` from its own cwd - where the server put the real flag - and prints it as the source line of main. One upload, one function name, flag printed by gdb itself.

## Lessons learned

1. When a service runs a tool on your input, think about what the tool can be *convinced* to do through its own configuration, not only about breaking out of it. gdb reads files through its source resolution - no shell needed.
2. The character filter is only as good as the characters it covers. Here a space and a slash were allowed, and gdb's `/s` modifier plus a path in debug info did the rest.
3. Debug info is attacker-controlled data: `#line` and `-fdebug-prefix-map` let you plant an arbitrary source file name and directory into an ELF.
4. Verify intermediate artifacts (here `DW_AT_comp_dir` via readelf) before spending a run against the remote: a wrong compile-dir means gdb looks in the wrong place.
5. Always reproduce the server environment locally first (copy flag.txt into the working dir, run the exact command) - the whole chain was proven here before touching the instance.
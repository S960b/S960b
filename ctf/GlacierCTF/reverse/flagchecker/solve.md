# Flagchecker: solving path

[Русская версия](solve.ru.md)

## 0. The plan

This is a reverse-engineering problem rather than a search over all possible flags. The key question is whether the executable already contains enough information to recover an accepted input.

The solution record from November 20, 2026 establishes the following chain:

1. Inspect the supplied ELF executable.
2. Identify the collection of 1,337 XOR-encoded strings and the byte key 0x12.
3. Decode candidate strings.
4. Identify the accepted candidate (recorded as entry No. 706).
5. Validate it using the original executable.

The recovered candidate had the form `gctf{<64 hexadecimal characters>}`. The original checker returned `The flag is valid!`. The full value is withheld.

## 1. Inspecting the challenge

The supplied archive was `flagchecker.tar.gz`; the executable was named `challenge`. An ELF (Executable and Linkable Format) file normally contains machine code, metadata, and data used by the program. A checker can contain encoded constants even when the corresponding plaintext never appears in a normal string listing.

For reproduction, unpack the original archive in a dedicated disposable Linux environment and locate the executable:

```sh
tar -tzf flagchecker.tar.gz
tar -xzf flagchecker.tar.gz
find . -type f -name challenge
file ./challenge
readelf -h ./challenge
readelf -S ./challenge
strings -a ./challenge | head
```

These are proposed reproduction commands, not a transcript of the original run. Adjust the path if the archive creates a subdirectory. The exact ELF architecture, section offsets, and original disassembly are unavailable.

A useful next step when the binary is available is to inspect the success-message references and the code that handles user input, for example in Ghidra or with `objdump -d ./challenge`. This helps establish whether an encoded value is used by the actual validation logic. Do not assume that every interesting string is a valid answer.

## 2. Understanding XOR

The retained solution reports a fixed single-byte key, `0x12` (18 in decimal). XOR combines two bit patterns, producing a 1 in each position where the input bits differ.

For a plaintext byte P, key K, and encoded byte C:

```
C = P XOR K
P = C XOR K
```

The second equation follows from XOR being its own inverse: applying the same key twice restores the original byte. No brute force over the flag itself is required once the encoding and key are known.

For the known CTF prefix, the transformation is:

| Plaintext | ASCII (hex) | XOR 0x12 (hex) |
| --- | --- | --- |
| g | 67 | 75 |
| c | 63 | 71 |
| t | 74 | 66 |
| f | 66 | 74 |
| { | 7b | 69 |

Thus the encoded prefix is `75 71 66 74 69` (ASCII `uqfti`). The closing brace, 0x7d, becomes 0x6f.

Even without knowing the key initially, a hypothesis can be tested using a known plaintext byte: if an encoded prefix begins with 0x75, and the corresponding plaintext is g (0x67), then 0x75 XOR 0x67 = 0x12. The remaining four prefix bytes provide an independent consistency check. This explains how one can recognize the encoding; the exact original key-discovery commands were not retained.

## 3. Extracting candidate strings

The reported data set comprised 1,337 XOR-encoded strings. The recovered accepted flag contained 64 hexadecimal digits inside the braces, so the complete candidate is 70 bytes: a five-byte prefix, 64 digits and one closing brace.

For the reconstructed solver, we can XOR every byte of the input file with 0x12 and search the resulting buffer using the regular expression:

```
gctf\{[0-9a-fA-F]{64}\}
```

Decoding the entire buffer does not modify the original file. Most decoded bytes will not be meaningful; the regular expression selects only the expected shape. Because XOR preserves byte positions, the match offset is also the offset in the original encoded file.

This method requires the encoded candidate to occur contiguously in the file. It does not require the ELF to be executed for extraction and uses no third-party Python packages. If the data are stored in another representation, split into pieces, or generated at runtime, use the actual checker disassembly to reconstruct them instead.

The reconstructed extractor reports candidates in file-offset order and deduplicates identical strings for validation. Its numbering is not necessarily the same as the program's internal array ordering.

## 4. Why candidate extraction is not enough

Finding a string that matches the flag format is not equivalent to finding the accepted flag. With 1,337 encoded strings in the binary, a format match alone cannot establish which value is correct.

The historical record identifies entry No. 706 as the accepted one. The retained summary does not establish whether the original numbering started at zero or one, whether all 1,337 entries had the exact same flag format, or how the entry was selected. Consequently, a reproduction must not blindly take the 706th regex match.

The original checker is the final local acceptance oracle: pass a candidate to the program, then check whether it emits the success message. This verifies the actual input rather than trusting its appearance. If the binary requires a particular input protocol, inspect its code or help output before using the optional verifier.

## 5. Reproduction using the reconstructed script

Use Python 3. No additional dependencies are required. From the challenge folder in the write-up repository:

```sh
python3 scripts/solve.py /path/to/challenge
python3 scripts/solve.py --self-test
```

The default mode reports the number of matching candidates and their file offsets, without displaying full flags.

If inspection establishes that the executable reads one line from standard input, run the optional local verifier in an isolated VM:

```sh
python3 scripts/solve.py /path/to/challenge --verify stdin
```

For an executable accepting the candidate as its first command-line argument:

```sh
python3 scripts/solve.py /path/to/challenge --verify argv
```

The exact input interface of the historical executable is not preserved; these are alternative modes, not a claim that either was used originally. The verifier runs a separate process per candidate, checks for the recorded success text and imposes a configurable timeout per invocation. It makes no network requests and does not use a shell. Add `--show` if you want the complete candidate printed locally.

The self-test uses two synthetic, encoded flag-shaped values, including a duplicate, to validate the extraction logic and byte offsets. It does not establish compatibility with the missing original ELF.

## 6. Result and evidence

The retained competition-time result records a complete flag, entry No. 706 and the following success text from the original program:

```text
The flag is valid!
```

This is a preserved result from the conversation, not a newly generated terminal log. The flag is masked here as `gctf{...}`. The original executable is currently unavailable, so the reconstructed script cannot be rerun against it as part of preparing this write-up. A platform submission receipt was not recovered.

## 7. Takeaway

The weakness is that a fixed, reversible byte transformation is obfuscation, not encryption that protects a secret from someone who has the executable. A known flag prefix provides a strong clue for identifying the transform; the checker itself distinguishes valid input from decoys.

The important distinction is between recovering plausible candidates and validating one. Static extraction and a local acceptance check address those two separate problems.

# Frostgate License Manager — Windows license-check virtualization

[Русская версия](solve.ru.md)

## Challenge and supplied program

The goal is to find a license accepted by `virt.exe`, a Windows x86-64 PE console application. The official handout `virt.tar.gz` includes `virt/virt.exe` (154,624 bytes) and `virt/sha256sum`. The built-in SHA-256 check for the executable passes.

Static examination identifies eight PE sections: `.text`, `.data`, `.rdata`, `.pdata`, `.bss`, `.CRT`, `.idata`, and a custom `.frost0` section (raw size `0x38e4`, file offset `0x22200`). The program contains the prompts:

```text
Frostgate License Manager  v3.1
Enter license key:
Checking license, please wait...
```

The file is PE32+ / x86-64, with entry-point RVA `0x16a0`, image base `0x100000000`, and was distributed as a **Delphi-compiled** Windows challenge. It was designed to run under both Wine and Windows.

## The actual historical solution

The November 20, 2026 solve conversation records the following independently relevant observations:

1. The valid license has **exactly 40 characters**, starts `gctf{` and ends with `}`.
2. License checking runs through **virtualized functions**; the protection should not be understood as a simple plaintext string comparison.
3. The reverse-engineering process checked possible **printable values by position**, arriving at **exactly one matching character for every position**.
4. A distinct decoy branch decrypted to `wrong{f1n4l1z3r_d3c0y_n0t_th3_r34l_0n3}`. It is explicitly *not* the accepted flag.
5. The successful result was a 40-character `gctf{...}` license, and the participant later confirmed platform acceptance.

The raw accepted value is deliberately not republished in the text, as for the other solved challenges in this repository.

## Why the misleading string matters

A common reversing error is to stop at the first string looking like a flag. Here the `wrong{` marker and the word `d3c0y` are an explicit warning: a recovered decryptor or finalizer may process a **false path**. A valid solution must follow the actual successful license-verification path, not merely find decryptable data.

The earlier analysis was based on per-character matching through the VM-protected logic. A direct execution test of the original binary under Windows/Wine would be a suitable independent verification where those tools are available. This write-up does **not** claim such a re-run in the present authoring environment.

## Inspecting the handout

The included [scripts/inspect_handout.py](scripts/inspect_handout.py) verifies the archive's SHA-256 file and extracts section names/offsets directly from PE headers, without importing or executing Windows code. This is a **static structural check only**, not a license-cracking tool.

For a fuller opcode-level reproduction, one would need the earlier trace of the virtualized routines, exact acceptance predicates and the per-position search script. Those artifacts were **not included in the handout and are not available in the retrieved historical context**. Consequently, precise VM opcodes, offsets of every predicate, and a working automatic key generator are *not claimed* here.

## Lessons

Virtualization and deceptive decryptable strings increase the cost of analysis but do not establish cryptographic key security. Keep candidate strings, decoy code paths and validated license acceptance separate in a reversing report. When the original solver is missing, describe the recorded result faithfully instead of inventing implementation details.

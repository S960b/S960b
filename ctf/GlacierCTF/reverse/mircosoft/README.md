# mircosoft

[Русская версия](README.ru.md) · [Technical write-up](solve.md)

**GlacierCTF 2026 · mircosoft · rev · 50 pts**

**Status: Solved — Flag accepted by platform (participant confirmation).** The accepted flag is redacted in public text as `gctf{...}`.

## Recovery from the administrative memory dump

The reverse task bundled a Windows executable, license.bin and an **administrator memory dump**. The original solve session recovered the embedded PE from `mircosoft.exe` and extracted host-fingerprint data from the dump. The preserved solver [solve.py](scripts/solve.py) expects these already-extracted materials at fixed paths; it is **not** a self-contained memory-dump parser.

It reads four 16-byte internal keys from the unpacked PE's .data region (`RVA 0x6000`, file offset `0x3e00`). The challenge uses a **custom TEA-like 32-round block cipher** with the delta `0x9e3779b9` and a 128-bit MAC state. Each 4-byte fingerprint fragment XORs into the state; paired 8-byte halves are processed with different embedded keys, with additional cross-half mixing.

The host fingerprint concatenates **CPUID-derived data (48 bytes), volume serial (4 bytes), registry MachineGuid (36 bytes), SMBIOS UUID (16 bytes) and network MAC address (6 bytes)** recovered from separate dump regions. The script computes a 16-byte MAC over the fingerprint using the two authentication keys and checks it against `license.bin[8:24]`, proving the recovered data corresponds to the signed/license-bound machine.

It then computes successive MAC states for CPU ID, volume+registry and UUID+MAC, applies the custom 128-bit state transform, and obtains the stream-encryption key. For eight 8-byte blocks it encrypts counters based on the license nonce with the custom TEA function and XORs the result against `license.bin[32:96]`. The historic run printed the decrypted plaintext and recovered the accepted `gctf{...}` flag.

**Important provenance:** the public `scripts/solve.py` is the original retained *final decryption stage*, written against local extracted binaries `unpacked_pe.bin` and `region0.bin`–`region4.bin`. The dump extraction and PE unpacker were performed earlier but their complete original scripts were not present in the retrieved files. Do not represent this stage as a universal one-command solver.

